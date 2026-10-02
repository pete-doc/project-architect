"""PostToolUse: после правки файла запускает форматтер, линтер и проверку типов его языка.

Агенту возвращаются только строки с ошибками (код выхода 2: Claude Code показывает stderr
агенту). Если ошибок нет, hook молчит. Инструмент, которого нет в проекте, пропускается.
Покрытие: Python (ruff, pyright), TypeScript/JavaScript (biome или prettier+eslint, tsc),
C# (dotnet format и сборка), PowerShell (PSScriptAnalyzer). Активен только в проектах
с CONSTITUTION.md.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from _common import (
    FILE_TOOLS,
    audit,
    get_str,
    is_managed,
    project_dir,
    read_input,
    target_paths,
)

HOOK = "post_edit_check"
STEP_TIMEOUT = 90
MAX_LINES = 30
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".claude", "bin", "obj"}

Keep = Callable[[str], bool]


@dataclass
class Step:
    name: str
    argv: list[str]
    keep: Keep | None  # None: вывод шага не показывается (форматтер)
    cwd: Path
    env: dict[str, str] = field(default_factory=dict[str, str])


def error_line(line: str) -> bool:
    return bool(re.search(r"\b(error|ошибка)\b", line, re.IGNORECASE))


def location_line(line: str) -> bool:
    return bool(re.search(r":\d+(:\d+)?[:\s-]", line))


def _node_bin(project: Path, name: str) -> str | None:
    suffixes = (".cmd", ".exe", "") if os.name == "nt" else ("",)
    for suffix in suffixes:
        candidate = project / "node_modules" / ".bin" / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


def _python_tool(name: str) -> list[str] | None:
    found = shutil.which(name)
    if found:
        return [found]
    if importlib.util.find_spec(name) is not None:
        return [sys.executable, "-m", name]
    return None


def _nearest(start: Path, project: Path, patterns: tuple[str, ...]) -> Path | None:
    folder = start.parent
    while True:
        for pattern in patterns:
            matches = sorted(folder.glob(pattern))
            if matches:
                return matches[0]
        if folder == project or project not in folder.parents:
            return None
        folder = folder.parent


def steps_for(file: Path, project: Path) -> list[Step]:
    suffix = file.suffix.lower()
    steps: list[Step] = []
    if suffix in {".py", ".pyi"}:
        ruff = _python_tool("ruff")
        if ruff:
            steps.append(Step("ruff format", [*ruff, "format", str(file)], error_line, project))
            steps.append(
                Step(
                    "ruff check",
                    [*ruff, "check", "--output-format", "concise", str(file)],
                    location_line,
                    project,
                )
            )
        pyright = _python_tool("pyright")
        if pyright:
            steps.append(
                Step("pyright", [*pyright, str(file)], lambda line: " - error" in line, project)
            )
    elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        biome = _node_bin(project, "biome")
        if biome:
            steps.append(
                Step("biome format", [biome, "format", "--write", str(file)], None, project)
            )
            steps.append(Step("biome lint", [biome, "lint", str(file)], location_line, project))
        else:
            prettier = _node_bin(project, "prettier")
            if prettier:
                steps.append(
                    Step("prettier", [prettier, "--write", str(file)], error_line, project)
                )
            eslint = _node_bin(project, "eslint")
            if eslint:
                steps.append(
                    Step("eslint", [eslint, "--format", "unix", str(file)], location_line, project)
                )
        tsc = _node_bin(project, "tsc")
        tsconfig = _nearest(file, project, ("tsconfig.json",))
        if tsc and tsconfig and suffix in {".ts", ".tsx"}:
            steps.append(
                Step(
                    "tsc",
                    [tsc, "--noEmit", "-p", str(tsconfig)],
                    lambda line: "error TS" in line,
                    project,
                )
            )
    elif suffix == ".cs":
        dotnet = shutil.which("dotnet")
        target = _nearest(file, project, ("*.csproj", "*.sln"))
        if dotnet and target:
            steps.append(
                Step(
                    "dotnet format",
                    [dotnet, "format", str(target), "--include", str(file)],
                    error_line,
                    project,
                )
            )
            steps.append(
                Step(
                    "dotnet build",
                    [dotnet, "build", str(target), "--nologo", "-v", "q", "-clp:NoSummary"],
                    lambda line: ": error " in line,
                    project,
                )
            )
    elif suffix in {".ps1", ".psm1", ".psd1"}:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            script = (
                "if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) { exit 0 }; "
                "$f = $env:PARCH_FILE; "
                "$t = [IO.File]::ReadAllText($f); "
                "$n = Invoke-Formatter -ScriptDefinition $t; "
                "$utf8 = New-Object Text.UTF8Encoding $false; "
                "if ($n -ne $t) { [IO.File]::WriteAllText($f, $n, $utf8) }; "
                "Invoke-ScriptAnalyzer -Path $f | ForEach-Object "
                '{ "{0}:{1}: {2} {3}" -f $_.ScriptName, $_.Line, $_.Severity, $_.Message }'
            )
            steps.append(
                Step(
                    "PSScriptAnalyzer",
                    [shell, "-NoProfile", "-NonInteractive", "-Command", script],
                    lambda line: bool(re.search(r":\d+: (Error|Warning)", line)),
                    project,
                    {"PARCH_FILE": str(file)},
                )
            )
    return steps


def _fingerprint(file: Path) -> str:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return ""


def run_step(step: Step) -> list[str]:
    """Запускает шаг и возвращает только строки с ошибками (пусто, если всё хорошо)."""
    try:
        result = subprocess.run(
            step.argv,
            cwd=step.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=STEP_TIMEOUT,
            check=False,
            env={**os.environ, **step.env},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f"{step.name}: не удалось запустить ({type(error).__name__})"]
    if step.keep is None:
        return []
    lines = [
        ln.rstrip() for ln in (result.stdout + "\n" + result.stderr).splitlines() if ln.strip()
    ]
    return [ln for ln in lines if step.keep(ln)]


def collect_errors(file: Path, project: Path) -> tuple[list[str], bool]:
    """Возвращает (строки с ошибками, были ли файл автоматически переформатирован)."""
    before = _fingerprint(file)
    errors: list[str] = []
    for step in steps_for(file, project):
        errors.extend(f"[{step.name}] {line}" for line in run_step(step))
    return errors, _fingerprint(file) != before


def main() -> None:
    try:
        data = read_input()
        project = project_dir(data)
        if get_str(data, "tool_name") not in FILE_TOOLS or not is_managed(project):
            sys.exit(0)
        problems: list[str] = []
        reformatted: list[str] = []
        for raw in target_paths(data):
            file = Path(raw)
            if not file.is_file() or _SKIP_PARTS & {p.lower() for p in file.parts}:
                continue
            errors, changed = collect_errors(file, project)
            problems.extend(errors)
            if changed:
                reformatted.append(file.name)
        notes = [f"Файл {name} автоматически переформатирован." for name in reformatted]
        if problems:
            shown = problems[:MAX_LINES]
            extra = len(problems) - len(shown)
            tail = [f"... и ещё {extra} строк"] if extra > 0 else []
            audit(project, data, HOOK, "errors", f"{len(problems)} строк с ошибками")
            sys.stderr.write(
                "Проверки после правки нашли ошибки, исправь их:\n"
                + "\n".join([*shown, *tail, *notes])
                + "\n"
            )
            sys.exit(2)
        audit(project, data, HOOK, "ok", ", ".join(target_paths(data)))
    except SystemExit:
        raise
    except Exception as error:
        # Проверка после правки не блокирует работу: лишь сообщаем, что она не отработала.
        sys.stderr.write(f"[{HOOK}] проверка не отработала: {type(error).__name__}: {error}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
