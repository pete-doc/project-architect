# ruff: noqa: E501
"""Правило «низкоуровневая библиотека подключается только в одном модуле» для C#, TypeScript и PowerShell (F15).

Правила лежат в `state/architecture.json` (его меняет только владелец):

    {"version": 1, "library_rules": [
      {"library": "System.Data", "language": "csharp", "only_in": ["src/Shop/Db"]},
      {"library": "node:fs", "language": "typescript", "only_in": ["src/db"]},
      {"library": "SqlServer", "language": "powershell", "only_in": ["modules/Db"]}
    ]}

Проверка `libraries` читает исходники языка (без тестов и сборочных папок) и находит подключения библиотеки: C# `using`,
TypeScript `import`/`require`, PowerShell `Import-Module`, `using module`, `#Requires -Modules`, `Add-Type -AssemblyName`.
Подключение вне `only_in` это провал с файлом и строкой. Правило, у которого путь `only_in` не существует (опечатка),
тоже провал: такое правило ничего не охватывало бы. Поиск по тексту, не по разбору кода: комментарии пропускаются.
Python проверяет import-linter (контракт `forbidden` с `ignore_imports`), здесь его нет.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

RULES_FILE = "state/architecture.json"
LANGUAGES = ("csharp", "typescript", "powershell")
SUFFIXES = {
    "csharp": (".cs",),
    "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs"),
    "powershell": (".ps1", ".psm1"),
}
SKIP_DIRS = {
    ".git", ".github", ".claude", ".venv", "venv", "env", "node_modules", "__pycache__", "bin", "obj",
    "build", "dist", "docs", "state", "tests", "test", "__tests__", "analysis", "archive", ".tox",
    "coverage", "site-packages",
}  # fmt: skip
TEST_FILE = re.compile(r"(\.test\.|\.spec\.|\.d\.ts$|Tests?\.cs$|\.tests?\.ps1$)", re.IGNORECASE)

CS_USING = re.compile(r"^\s*(?:global\s+)?using\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)\s*;")
TS_MODULE = re.compile(
    r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\(\s*|^\s*import\s+)['"]([^'"]+)['"]"""
)
PS_MODULE = (
    re.compile(r"\bImport-Module\s+(?:-Name\s+)?['\"]?([\w.\-]+)", re.IGNORECASE),
    re.compile(r"^\s*using\s+module\s+['\"]?([\w.\-]+)", re.IGNORECASE),
    re.compile(r"^\s*#Requires\s+-Modules?\s+['\"]?([\w.\-]+)", re.IGNORECASE),
    re.compile(r"\bAdd-Type\s+-AssemblyName\s+['\"]?([\w.\-]+)", re.IGNORECASE),
)


@dataclass(frozen=True)
class Rule:
    library: str
    language: str
    only_in: tuple[str, ...]


@dataclass(frozen=True)
class Use:
    path: str
    line: int


def norm(path: str) -> str:
    return PurePosixPath(path.strip().replace("\\", "/")).as_posix().removeprefix("./").rstrip("/")


def read_rules(project: Path) -> tuple[list[Rule], list[str]]:
    """(правила, проблемы формата). Нет файла: правил нет, проблем нет."""
    path = project / RULES_FILE
    if not path.is_file():
        return [], []
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as error:
        return [], [f"{RULES_FILE}: не удалось прочитать ({error})"]
    raw = cast("dict[str, object]", data).get("library_rules") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return [], [f"{RULES_FILE}: нет списка library_rules"]
    rules: list[Rule] = []
    problems: list[str] = []
    for index, item in enumerate(cast("list[object]", raw), start=1):
        entry = cast("dict[str, object]", item) if isinstance(item, dict) else {}
        library = str(entry.get("library") or "").strip()
        language = str(entry.get("language") or "").strip()
        only = entry.get("only_in")
        only_in = (
            [norm(str(p)) for p in cast("list[object]", only)] if isinstance(only, list) else []
        )
        if not library or language not in LANGUAGES or not [p for p in only_in if p]:
            problems.append(
                f"{RULES_FILE}, правило {index}: нужны library, language ({', '.join(LANGUAGES)}) "
                "и непустой список only_in"
            )
            continue
        rules.append(Rule(library, language, tuple(p for p in only_in if p)))
    return rules, problems


def source_files(project: Path, language: str) -> list[Path]:
    found: list[Path] = []
    for suffix in SUFFIXES[language]:
        for path in sorted(project.rglob(f"*{suffix}")):
            parts = path.relative_to(project).parts
            if any(p in SKIP_DIRS or p.endswith((".Tests", ".tests")) for p in parts[:-1]):
                continue
            if TEST_FILE.search(path.name):
                continue
            found.append(path)
    return found


def modules_in(language: str, line: str) -> list[str]:
    """Имена библиотек, подключённых строкой (комментарии уже отброшены)."""
    if language == "csharp":
        found = CS_USING.match(line)
        return [found.group(1)] if found else []
    if language == "typescript":
        return [m.removeprefix("node:") for m in TS_MODULE.findall(line)]
    return [m for pattern in PS_MODULE for m in pattern.findall(line)]


def is_comment(language: str, line: str) -> bool:
    stripped = line.lstrip()
    if language == "powershell":
        return stripped.startswith("#") and not stripped.lower().startswith("#requires")
    return stripped.startswith(("//", "/*", "*"))


def matches(library: str, module: str, language: str) -> bool:
    lib = library.removeprefix("node:") if language == "typescript" else library
    if language == "powershell":
        return module.lower() == lib.lower()
    return module == lib or module.startswith(lib + ("." if language == "csharp" else "/"))


def uses_of(project: Path, rule: Rule) -> list[Use]:
    found: list[Use] = []
    for path in source_files(project, rule.language):
        rel = path.relative_to(project).as_posix()
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, start=1):
            if is_comment(rule.language, line):
                continue
            if any(
                matches(rule.library, m, rule.language) for m in modules_in(rule.language, line)
            ):
                found.append(Use(rel, number))
    return found


def allowed(path: str, rule: Rule) -> bool:
    return any(path == base or path.startswith(base + "/") for base in rule.only_in)


def check(project: Path, language: str) -> tuple[list[str], list[str]]:
    """(провалы, пометки) для правил одного языка."""
    rules, problems = read_rules(project)
    notes: list[str] = []
    mine = [r for r in rules if r.language == language]
    for rule in mine:
        missing = [p for p in rule.only_in if not (project / p).exists()]
        if missing:
            problems.append(
                f"Правило «{rule.library}»: пути only_in нет в проекте ({', '.join(missing)}). "
                "Правило с опечаткой ничего не охватывает, поэтому оно считается ошибкой."
            )
            continue
        uses = uses_of(project, rule)
        outside = [u for u in uses if not allowed(u.path, rule)]
        if outside:
            problems.append(
                f"Библиотека «{rule.library}» подключена вне разрешённого модуля ({', '.join(rule.only_in)}):"
            )
            problems += [f"  {u.path}:{u.line}" for u in outside[:20]]
            if len(outside) > 20:
                problems.append(f"  ... и ещё {len(outside) - 20}")
        elif not uses:
            notes.append(f"Библиотека «{rule.library}» пока нигде не подключена.")
    if not problems:
        notes.append(
            f"Правил «библиотека в одном модуле» для {language}: {len(mine)}, нарушений нет."
        )
    return problems, notes
