# ruff: noqa: E501
"""Инструменты по языкам из docs/design/analyze-existing.md (таблица «Инструменты по языкам»).

Снимок исходного состояния: какие инструменты из таблицы настроены в проекте (по файлам, без запуска) и, если
владелец разрешил, результаты их запуска в режиме «только отчёт» (`baseline`): запуск по каждому проекту C#, вывод
разобран в числа, неприкосновенные зоны исключены.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, cast


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


# Правила стандарта (AGENTS.md продукта): ruff меряет не чужой набор правил по умолчанию, а E,F,I,B,UP, строка 100.
RUFF_RULES = ("--isolated", "--select", "E,F,I,B,UP", "--line-length", "100")
JSCPD_VERSION = "5.4.0"  # версия, на которой проверен разбор отчёта
PSA_SCRIPT = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Invoke-ScriptAnalyzer -Path . -Recurse | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.ScriptPath,$_.Line,$_.Severity,$_.RuleName }"
)
# -EncodedCommand: кавычки и «|» в тексте команды при передаче через командную строку Windows ломали разбор параметров.
PSA_ARGV = (
    "-NoProfile",
    "-NonInteractive",
    "-EncodedCommand",
    base64.b64encode(PSA_SCRIPT.encode("utf-16-le")).decode("ascii"),
)
GENERATED_GLOBS = (
    "**/*.g.cs",
    "**/*.Designer.cs",
    "**/*.designer.cs",
    "**/*decompiled*/**",
    "**/*Decompiled*/**",
)
JSCPD_FORMATS = {
    "python": "python",
    "csharp": "csharp",
    "typescript": "typescript",
    "powershell": "powershell",
}
# Только чтение: ни одна команда не правит файлы проекта (кэши отключены). Запуск идёт только по просьбе владельца.
RUNNABLE: tuple[Runnable, ...] = (
    Runnable("ruff", "python", "ruff check (правила стандарта)", "ruff", ("check", "--no-cache", *RUFF_RULES, "--output-format", "concise"), "замечания; отдельно число автоисправимых"),
    Runnable("pyright", "python", "pyright", "pyright", ("--outputjson",), "ошибки типов"),
    Runnable("vulture", "python", "vulture", "vulture", ("--min-confidence", "80"), "кандидаты в мёртвый код"),
    Runnable("jscpd", "any", "jscpd", "jscpd", ("--reporters", "json"), "процент дублирования"),
    Runnable("dotnet-build", "csharp", "dotnet build", "dotnet", ("build", "--nologo", "-v", "q"), "ошибки и предупреждения сборки по проектам"),
    Runnable("dotnet-format", "csharp", "dotnet format --verify-no-changes", "dotnet", ("format", "--verify-no-changes", "--no-restore", "-v", "q"), "нарушения форматирования по проектам"),
    Runnable("tsc", "typescript", "tsc --noEmit", "tsc", ("--noEmit",), "ошибки типов"),
    Runnable("psscriptanalyzer", "powershell", "PSScriptAnalyzer", "pwsh", PSA_ARGV, "замечания по важности"),
)  # fmt: skip
OUTPUT_TAIL = 6000
TIMEOUT = 600
BUILD_DIRS = ("bin", "obj")  # побочный след разрешённой сборки C#
SKIP_WALK = {".git", "node_modules", "venv", ".venv", "bin", "obj", "dist", "build", "__pycache__"}
STEP_PATTERN = re.compile(r"PostBuildEvent|<Exec\b|<Copy\b|<Move\b|<Delete\b|<RemoveDir\b")

Runner = Callable[[list[str], Path, int], tuple[int, str, str]]


def subprocess_runner(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "DOTNET_CLI_UI_LANGUAGE": "en",  # разбор вывода рассчитан на английский текст
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
    }
    done = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, env=env,
    )  # fmt: skip
    return done.returncode, done.stdout, done.stderr


def list_files(project: Path) -> list[str]:
    """Файлы проекта (относительные пути с «/»), без служебных и сборочных папок."""
    found: list[str] = []
    for folder, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_WALK]
        found += [(Path(folder) / n).relative_to(project).as_posix() for n in names]
    return sorted(found)


def dotnet_targets(rel: list[str]) -> list[str]:
    """Что собирать: решения (.sln/.slnx), а если их нет — каждый .csproj (в корне проекта их часто нет)."""
    own = [p for p in rel if not set(p.split("/")[:-1]) & set(BUILD_DIRS)]
    slns = [p for p in own if p.lower().endswith((".sln", ".slnx"))]
    if slns:
        return sorted(slns)
    return sorted(p for p in own if p.lower().endswith(".csproj"))


def postbuild_steps(project: Path, rel: list[str]) -> list[str]:
    """Шаги после сборки (копирование, запуск команд) в .csproj, .props, .targets и Directory.Build.*."""
    found: list[str] = []
    for path in rel:
        name = path.rsplit("/", 1)[-1]
        if not (
            name.lower().endswith((".csproj", ".props", ".targets"))
            or name.startswith("Directory.Build")
        ):
            continue
        try:
            text = (project / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = STEP_PATTERN.search(text)
        if match:
            found.append(f"{path}: {match.group(0)}")
    return found


def zone_globs(zones: tuple[str, ...]) -> list[str]:
    return [z.strip("/") for z in zones if z.strip("/")]


def number(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def parse_ruff(text: str) -> dict[str, Any]:
    rules = Counter(re.findall(r"^\S+:\d+:\d+: ([A-Z]+\d+)", text, re.MULTILINE))
    total = number(r"Found (\d+) errors?", text)
    if total is None:
        if "All checks passed" not in text and not rules:
            return {}  # вывод не похож на вывод ruff
        total = 0 if "All checks passed" in text else sum(rules.values())
    return {
        "issues": total,
        "auto_fixable": number(r"(\d+) fixable", text) or 0,
        "top_rules": [{"rule": r, "count": c} for r, c in rules.most_common(5)],
    }


def parse_pyright(text: str) -> dict[str, Any]:
    start = text.find("{")
    try:
        summary = json.loads(text[start:])["summary"] if start >= 0 else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    if not isinstance(summary, dict):
        return {}
    counts = cast("dict[str, Any]", summary)
    return {
        "errors": int(counts.get("errorCount", 0)),
        "warnings": int(counts.get("warningCount", 0)),
        "files": int(counts.get("filesAnalyzed", 0)),
    }


def parse_build(text: str, code: int) -> dict[str, Any]:
    unique_warn = len({m for m in re.findall(r"^.*: warning [A-Z]+\d+.*$", text, re.MULTILINE)})
    unique_err = len({m for m in re.findall(r"^.*: error [A-Z]+\d+.*$", text, re.MULTILINE)})
    warnings = number(r"(\d+) Warning\(s\)", text)
    errors = number(r"(\d+) Error\(s\)", text)
    return {
        "ok": code == 0,
        "warnings": warnings if warnings is not None else unique_warn,
        "errors": errors if errors is not None else unique_err,
    }


def parse_format(text: str) -> dict[str, Any]:
    hits = re.findall(r"^(.+?)\(\d+,\d+\): (?:warning|error) ", text, re.MULTILINE)
    return {"violations": len(hits), "files": len(set(hits))}


def parse_psa(text: str, zones: list[str], project: Path) -> dict[str, Any]:
    by_severity: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    parsed_lines = 0
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        parsed_lines += 1
        try:
            rel_path = Path(parts[0]).resolve().relative_to(project.resolve()).as_posix()
        except (ValueError, OSError):
            rel_path = parts[0].replace(chr(92), "/")
        if any(rel_path == z or rel_path.startswith(z + "/") for z in zones):
            continue
        by_severity[parts[2]] += 1
        rules[parts[3]] += 1
    if not parsed_lines and text.strip():
        return {}  # вместо замечаний пришёл текст ошибки (нет модуля, сбой запуска): это не «ноль замечаний»
    return {
        "errors": by_severity["Error"],
        "warnings": by_severity["Warning"],
        "information": by_severity["Information"],
        "top_rules": [{"rule": r, "count": c} for r, c in rules.most_common(5)],
    }


def parse_jscpd(report: Path) -> dict[str, Any]:
    try:
        stats = json.loads((report / "jscpd-report.json").read_text(encoding="utf-8"))["statistics"]
        raw = dict(stats["formats"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}
    formats: dict[str, Any] = {}
    for name, data in raw.items():
        formats[str(name)] = {
            "lines": data.get("lines", 0),
            "duplicated_lines": data.get("duplicatedLines", 0),
            "clones": data.get("clones", 0),
            "percentage": round(float(data.get("percentage", 0)), 1),
        }
    lines = sum(f["lines"] for f in formats.values())
    duplicated = sum(f["duplicated_lines"] for f in formats.values())
    return {
        "lines": lines,
        "clones": sum(f["clones"] for f in formats.values()),
        "duplicated_lines": duplicated,
        "percentage": round(100 * duplicated / lines, 1) if lines else 0,
        "by_format": formats,
    }


def resolve_jscpd(
    which: Callable[[str], str | None],
    runner: Runner,
    allow_download: bool,
    home: Path | None = None,
) -> tuple[list[str], str]:
    """Как запустить jscpd: из PATH; иначе из временной папки вне проекта; иначе (с разрешением) скачать пакет туда.

    Через `npx` не запускаем: на части машин `npx` не запускает ни один пакет (npm 11 на Windows: `"node" is not
    recognized`). Тот же пакет и та же версия ставятся командой `npm install` в папку во временном каталоге.
    """
    folder = home or Path(tempfile.gettempdir()) / f"parch-jscpd-{JSCPD_VERSION}"
    launcher = folder / "node_modules" / "jscpd" / "run-jscpd.js"
    node, npm = which("node"), which("npm")
    if not (node and npm):
        return [], "не установлен (jscpd; нет и npm для скачивания)"
    if launcher.is_file():
        return [node, str(launcher)], ""
    if not allow_download:
        return [], (
            f"не запускался: jscpd не установлен; можно скачать пакет jscpd@{JSCPD_VERSION} (npm install во временную папку вне проекта), "
            "нужно разрешение владельца (allow_download)"
        )
    folder.mkdir(parents=True, exist_ok=True)
    code, out, err = runner(
        [
            npm,
            "install",
            "--no-audit",
            "--no-fund",
            "--silent",
            "--prefix",
            str(folder),
            f"jscpd@{JSCPD_VERSION}",
        ],
        folder,
        TIMEOUT,
    )
    if code != 0 or not launcher.is_file():
        return [], f"не установился (npm install, код {code}): {(out + err).strip()[-200:]}"
    return [node, str(launcher)], ""


def run_baseline(
    project: Path,
    run: list[str],
    languages: dict[str, int],
    runner: Runner = subprocess_runner,
    which: Callable[[str], str | None] = shutil.which,
    *,
    rel: list[str] | None = None,
    protected: tuple[str, ...] = (),
    report_dir: Path | None = None,
    allow_download: bool = False,
    allow_build_steps: bool = False,
    jscpd_home: Path | None = None,
) -> list[dict[str, Any]]:
    """Запускает разрешённые инструменты в режиме «только отчёт» и возвращает результаты (в файлы проекта не пишет).

    `protected`: неприкосновенные зоны, их файлы инструменты не читают (где инструмент это умеет).
    `allow_download`: можно скачать пакет `jscpd` (нужно разрешение владельца); он ставится во временную папку вне проекта.
    `allow_build_steps`: можно собирать C#, даже если в проектах есть шаги после сборки.
    """
    known = {r.key: r for r in RUNNABLE}
    files = rel if rel is not None else list_files(project)
    zones = zone_globs(protected)
    jscpd_out = (report_dir or project / "parch-analysis") / "baseline" / "jscpd"
    results: list[dict[str, Any]] = []
    for key in run:
        tool = known.get(key)
        if tool is None:
            raise ValueError(f"неизвестный инструмент {key}: допустимы {', '.join(sorted(known))}")
        if tool.language != "any" and not languages.get(tool.language):
            results.append({"tool": key, "status": "пропущен: в проекте нет этого языка"})
            continue
        found = which(tool.executable)
        prefix: list[str] = [found] if found else []
        if not found and key == "jscpd":
            prefix, problem = resolve_jscpd(which, runner, allow_download, jscpd_home)
            if problem:
                results.append({"tool": key, "status": problem})
                continue
        if not prefix:
            results.append({"tool": key, "status": f"не установлен ({tool.executable})"})
            continue
        # точка («весь проект») нужна не каждому: PowerShell и tsc принимают её за лишний параметр
        jobs: list[tuple[str, list[str]]] = [
            ("", [*prefix, *tool.argv, *(["."] if key == "pyright" else [])])
        ]
        if key == "ruff":
            excluded = [*zones, "bin", "obj"]
            jobs = [
                (
                    "",
                    [
                        *prefix,
                        *tool.argv,
                        *[a for z in excluded for a in ("--extend-exclude", z)],
                        ".",
                    ],
                )
            ]
        elif key == "vulture":
            patterns = ",".join(p for z in zones for p in (f"{z}/*", f"*/{z}/*"))
            extra = ["--exclude", patterns] if patterns else []
            jobs = [("", [*prefix, ".", *tool.argv, *extra])]
        elif key == "jscpd":
            ignore = [
                "**/bin/**",
                "**/obj/**",
                "**/node_modules/**",
                "**/.git/**",
                "parch-analysis*/**",
                *GENERATED_GLOBS,
                *[f"{z}/**" for z in zones],
            ]
            formats = ",".join(JSCPD_FORMATS[lang] for lang in JSCPD_FORMATS if languages.get(lang))
            (jscpd_out / "jscpd-report.json").unlink(
                missing_ok=True
            )  # старый отчёт не должен сойти за новый
            jobs = [
                (
                    "",
                    [
                        *prefix,
                        *tool.argv,
                        "--output",
                        str(jscpd_out),
                        "--format",
                        formats,
                        "--ignore",
                        ",".join(ignore),
                        ".",
                    ],
                )
            ]
        elif key in {"dotnet-build", "dotnet-format"}:
            targets = dotnet_targets(files)
            if not targets:
                results.append({"tool": key, "status": "пропущен: нет .sln и .csproj"})
                continue
            steps = postbuild_steps(project, files)
            if key == "dotnet-build" and steps and not allow_build_steps:
                results.append(
                    {
                        "tool": key,
                        "status": "не запускался: в проектах есть шаги после сборки ("
                        + "; ".join(steps[:3])
                        + "); нужно решение владельца (allow_build_steps)",
                    }
                )
                continue
            jobs = [(t, [prefix[0], tool.argv[0], t, *tool.argv[1:]]) for t in targets]
        started = time.monotonic()
        outputs: list[tuple[str, int, str]] = []
        timed_out = False
        for label, argv in jobs:
            try:
                code, out, err = runner(argv, project, TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                break
            outputs.append((label, code, (out + chr(10) + err).strip()))
        if timed_out:
            results.append({"tool": key, "status": f"не уложился в {TIMEOUT} с"})
            continue
        text = chr(10).join(
            (f"== {label}{chr(10)}" if label else "") + body for label, _, body in outputs
        )
        metrics = summarize(key, outputs, zones, project, jscpd_out)
        results.append(
            {
                "tool": key,
                "label": tool.label,
                "status": "выполнен",
                "returncode": max(code for _, code, _ in outputs),
                "seconds": round(time.monotonic() - started),
                "meaning": tool.summary,
                "lines": len(text.splitlines()),
                "metrics": metrics,
                "output_tail": text[-OUTPUT_TAIL:],
            }
        )
    return results


def summarize(
    key: str,
    outputs: list[tuple[str, int, str]],
    zones: list[str],
    project: Path,
    jscpd_out: Path,
) -> dict[str, Any]:
    """Вывод инструмента в числа (пустой словарь, если вывод не разобрался)."""
    text = chr(10).join(body for _, _, body in outputs)
    if key == "ruff":
        return parse_ruff(text)
    if key == "pyright":
        return parse_pyright(text)
    if key == "vulture":
        return {"candidates": len(re.findall(r"^\S.*:\d+: ", text, re.MULTILINE))}
    if key == "jscpd":
        return parse_jscpd(jscpd_out)
    if key == "tsc":
        return {"errors": len(re.findall(r"error TS\d+", text))}
    if key == "psscriptanalyzer":
        return parse_psa(text, zones, project)
    parsed = [
        (label, parse_build(body, code) if key == "dotnet-build" else parse_format(body), code)
        for label, code, body in outputs
    ]
    if key == "dotnet-build":
        return {
            "projects": len(parsed),
            "built": sum(1 for _, p, _ in parsed if p["ok"]),
            "failed": [label for label, p, _ in parsed if not p["ok"]],
            "warnings": sum(int(p["warnings"]) for _, p, _ in parsed),
            "errors": sum(int(p["errors"]) for _, p, _ in parsed),
        }
    if any(code not in (0, 2) and not p["violations"] for _, p, code in parsed):
        return {}  # dotnet format не отработал (код не «чисто» и не «есть нарушения»): это не «ноль нарушений»
    return {
        "projects": len(parsed),
        "violations": sum(int(p["violations"]) for _, p, _ in parsed),
        "files": sum(int(p["files"]) for _, p, _ in parsed),
        "clean_projects": sum(1 for _, p, _ in parsed if not p["violations"]),
    }


def write_baseline(report_dir: Path, results: list[dict[str, Any]]) -> list[str]:
    """Сохраняет результаты в <папка отчёта>/baseline/ в машинном формате (JSON). Только внутри папки отчёта."""
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
