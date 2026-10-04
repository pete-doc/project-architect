# ruff: noqa: E501
"""Инструменты по языкам из docs/design/analyze-existing.md (таблица «Инструменты по языкам»).

Снимок исходного состояния: какие инструменты из таблицы настроены в проекте (по файлам, без запуска) и, если
владелец разрешил, результаты их запуска в режиме «только отчёт» (`baseline`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple


class Tool(NamedTuple):
    task: str  # задача из таблицы
    name: str
    markers: tuple[
        str, ...
    ]  # файлы или фрагменты настроек, по которым видно, что инструмент подключён


TABLE: dict[str, tuple[Tool, ...]] = {
    "python": (
        Tool(
            "Проверка типов",
            "pyright или mypy",
            ("pyrightconfig.json", "[tool.pyright]", "mypy.ini", "[tool.mypy]", "[mypy]"),
        ),
        Tool("Линтер и форматирование", "ruff", ("ruff.toml", ".ruff.toml", "[tool.ruff")),
        Tool(
            "Правила архитектуры",
            "import-linter",
            (".importlinter", "[tool.importlinter]", "[importlinter]"),
        ),
        Tool(
            "Мёртвый код",
            "vulture, deptry",
            ("[tool.vulture]", "[tool.deptry]", "vulture", "deptry"),
        ),
        Tool("Дублирование", "jscpd", (".jscpd.json", "jscpd")),
        Tool(
            "Тесты и покрытие",
            "pytest + coverage.py",
            ("pytest.ini", "[tool.pytest", ".coveragerc", "[tool.coverage", "[tool:pytest]"),
        ),
    ),
    "typescript": (
        Tool("Проверка типов", "tsc --noEmit (strict)", ("tsconfig.json",)),
        Tool(
            "Линтер и форматирование",
            "ESLint + Prettier или Biome",
            ("eslint.config", ".eslintrc", "biome.json", ".prettierrc"),
        ),
        Tool(
            "Правила архитектуры",
            "dependency-cruiser",
            (".dependency-cruiser", "dependency-cruiser"),
        ),
        Tool("Мёртвый код", "knip", ("knip.json", "knip.config", '"knip"')),
        Tool("Дублирование", "jscpd", (".jscpd.json", "jscpd")),
        Tool(
            "Тесты и покрытие",
            "Vitest или Jest",
            ("vitest.config", "jest.config", '"vitest"', '"jest"'),
        ),
    ),
    "csharp": (
        Tool(
            "Проверка типов",
            "компилятор: nullable и предупреждения как ошибки",
            ("<Nullable>enable", "TreatWarningsAsErrors"),
        ),
        Tool(
            "Линтер и форматирование",
            ".NET-анализаторы, .editorconfig, dotnet format, Roslynator",
            (".editorconfig", "Roslynator", "AnalysisLevel", "EnableNETAnalyzers"),
        ),
        Tool("Правила архитектуры", "ArchUnitNET или NetArchTest", ("ArchUnitNET", "NetArchTest")),
        Tool(
            "Мёртвый код",
            "анализаторы неиспользуемых членов (IDE0051/IDE0052), Roslynator",
            ("IDE0051", "IDE0052", "Roslynator"),
        ),
        Tool("Дублирование", "jscpd", (".jscpd.json", "jscpd")),
        Tool(
            "Тесты и покрытие", "xUnit + coverlet", ("xunit", "coverlet", "Microsoft.NET.Test.Sdk")
        ),
    ),
    "powershell": (
        Tool("Проверка типов", "нет (язык без статических типов)", ()),
        Tool(
            "Линтер и форматирование",
            "PSScriptAnalyzer",
            ("PSScriptAnalyzerSettings.psd1", "PSScriptAnalyzer"),
        ),
        Tool("Правила архитектуры", "своя проверка (готового инструмента нет)", ()),
        Tool("Мёртвый код", "инструмента нет; косвенно через покрытие Pester", ()),
        Tool("Дублирование", "jscpd (поддержку проверить на пилоте)", (".jscpd.json", "jscpd")),
        Tool("Тесты и покрытие", "Pester", ("Pester", ".Tests.ps1")),
    ),
}
CONFIG_SUFFIXES = {".toml", ".ini", ".cfg", ".json", ".yml", ".yaml", ".props", ".csproj", ".psd1"}
CONFIG_NAMES = {
    ".editorconfig",
    ".importlinter",
    ".jscpd.json",
    ".coveragerc",
    ".ruff.toml",
    ".prettierrc",
}
MAX_CONFIG_BYTES = 400_000


def config_files(project: Path, rel: list[str]) -> list[tuple[str, str]]:
    """Файлы настроек проекта: (путь, текст). Только небольшие файлы известных типов."""
    found: list[tuple[str, str]] = []
    for path in rel:
        name = path.rsplit("/", 1)[-1]
        suffix = Path(name).suffix.lower()
        if (
            name in CONFIG_NAMES
            or suffix in CONFIG_SUFFIXES
            or name.startswith(
                ("tsconfig", "eslint.config", "vitest.config", "jest.config", "requirements")
            )
        ):
            file = project / path
            try:
                if file.stat().st_size <= MAX_CONFIG_BYTES:
                    found.append((path, file.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return found


def detect(
    project: Path, rel: list[str], languages: dict[str, int]
) -> dict[str, list[dict[str, Any]]]:
    """Для каждого языка проекта: какие инструменты из таблицы подключены (без запуска)."""
    configs = config_files(project, rel)
    names = {p.rsplit("/", 1)[-1].lower() for p in rel}
    result: dict[str, list[dict[str, Any]]] = {}
    for language, tools in TABLE.items():
        if not languages.get(language):
            continue
        rows: list[dict[str, Any]] = []
        for tool in tools:
            evidence = ""
            for marker in tool.markers:
                lowered = marker.lower()
                if any(n == lowered or n.startswith(lowered) for n in names):
                    evidence = marker
                    break
                hit = next((p for p, text in configs if marker in text), None)
                if hit:
                    evidence = f"{marker} в {hit}"
                    break
            rows.append(
                {
                    "task": tool.task,
                    "tool": tool.name,
                    "configured": bool(evidence),
                    "evidence": evidence,
                }
            )
        result[language] = rows
    return result


class Runnable(NamedTuple):
    key: str
    language: str
    label: str
    executable: str
    argv: tuple[str, ...]  # без исполняемого файла
    summary: str  # как понимать результат


# Только чтение: ни одна команда не правит файлы проекта (кэши отключены). Запуск идёт только по просьбе владельца.
RUNNABLE: tuple[Runnable, ...] = (
    Runnable("ruff", "python", "ruff check", "ruff", ("check", "--no-cache", "--output-format", "concise", "."), "строки с ошибками"),
    Runnable("pyright", "python", "pyright", "pyright", ("--outputjson",), "ошибки типов"),
    Runnable("vulture", "python", "vulture", "vulture", (".", "--min-confidence", "80"), "кандидаты в мёртвый код"),
    Runnable("jscpd", "any", "jscpd", "jscpd", ("--silent", "--reporters", "console", "."), "процент дублирования"),
    Runnable("dotnet-build", "csharp", "dotnet build", "dotnet", ("build", "--nologo", "-v", "q"), "ошибки и предупреждения сборки"),
    Runnable("dotnet-format", "csharp", "dotnet format --verify-no-changes", "dotnet", ("format", "--verify-no-changes", "--no-restore"), "нарушения форматирования"),
    Runnable("tsc", "typescript", "tsc --noEmit", "tsc", ("--noEmit",), "ошибки типов"),
    Runnable("psscriptanalyzer", "powershell", "PSScriptAnalyzer", "pwsh", ("-NoProfile", "-Command", "Invoke-ScriptAnalyzer -Path . -Recurse | ForEach-Object { '{0}:{1} {2} {3}' -f $_.ScriptName,$_.Line,$_.Severity,$_.Message }"), "замечания"),
)  # fmt: skip
OUTPUT_TAIL = 6000
TIMEOUT = 600
BUILD_DIRS = ("bin", "obj")  # побочный след разрешённой сборки C#

Runner = Callable[[list[str], Path, int], tuple[int, str, str]]


def subprocess_runner(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    done = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )  # fmt: skip
    return done.returncode, done.stdout, done.stderr


def run_baseline(
    project: Path,
    run: list[str],
    languages: dict[str, int],
    runner: Runner = subprocess_runner,
    which: Callable[[str], str | None] = shutil.which,
) -> list[dict[str, Any]]:
    """Запускает разрешённые инструменты в режиме «только отчёт» и возвращает результаты (в файлы не пишет)."""
    known = {r.key: r for r in RUNNABLE}
    results: list[dict[str, Any]] = []
    for key in run:
        tool = known.get(key)
        if tool is None:
            raise ValueError(f"неизвестный инструмент {key}: допустимы {', '.join(sorted(known))}")
        if tool.language != "any" and not languages.get(tool.language):
            results.append({"tool": key, "status": "пропущен: в проекте нет этого языка"})
            continue
        found = which(tool.executable)
        if not found:
            results.append({"tool": key, "status": f"не установлен ({tool.executable})"})
            continue
        argv = [found, *tool.argv]
        started = time.monotonic()
        try:
            code, out, err = runner(argv, project, TIMEOUT)
        except subprocess.TimeoutExpired:
            results.append({"tool": key, "status": f"не уложился в {TIMEOUT} с"})
            continue
        text = (out + chr(10) + err).strip()
        results.append(
            {
                "tool": key,
                "label": tool.label,
                "status": "выполнен",
                "returncode": code,
                "seconds": round(time.monotonic() - started),
                "meaning": tool.summary,
                "lines": len(text.splitlines()),
                "output_tail": text[-OUTPUT_TAIL:],
            }
        )
    return results


def write_baseline(report_dir: Path, results: list[dict[str, Any]]) -> list[str]:
    """Сохраняет результаты в parch-analysis/baseline/ в машинном формате (JSON). Только внутри папки отчёта."""
    folder = report_dir / "baseline"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for result in results:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(result["tool"])) + ".json"
        (folder / name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8"
        )
        written.append(f"{report_dir.name}/baseline/{name}")
    return written
