# ruff: noqa: E501
"""Кандидаты в мёртвый код и вопросы владельцу (docs/design/analyze-existing.md, «Что анализ не делает и почему»).

Анализ ничего не удаляет: найденное попадает в QUESTIONS.md. По каждому кандидату три вопроса: вызывают ли его снаружи,
нет ли рефлексии или динамического вызова, нет ли запуска планировщиком. Поиск приблизительный: имя, которое в проекте
встречается один раз (в самом объявлении), считается кандидатом.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {
    ".py", ".cs", ".ps1", ".psm1", ".psd1", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml", ".md", ".csproj",
    ".props", ".sh", ".cmd", ".bat", ".toml", ".cfg", ".ini", ".xml",
}  # fmt: skip
MAX_FILE_BYTES = 1_500_000
MAX_CANDIDATES = 50
SKIP_NAMES = {"main", "Main", "setup", "teardown", "setUp", "tearDown"}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PS_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)+")
CS_DECL = re.compile(
    r"^\s*(?:public|internal)\s+(?:static\s+|sealed\s+|abstract\s+|partial\s+|readonly\s+)*"
    r"(?:class|record|struct|interface|enum)\s+(\w+)",
    re.MULTILINE,
)
CS_METHOD = re.compile(
    r"^\s*(?:public|internal)\s+(?:static\s+|async\s+|virtual\s+)*[\w<>\[\],.?]+\s+(\w+)\s*\(",
    re.MULTILINE,
)
PS_FUNCTION = re.compile(r"^\s*function\s+([A-Za-z][\w-]*)", re.MULTILINE | re.IGNORECASE)
QUESTIONS = (
    "Вызывается ли снаружи проекта (другой скрипт, внешняя программа, плагин, документация)?",
    "Нет ли рефлексии или динамического вызова по имени (C# reflection, `getattr`, строка с именем)?",
    "Нет ли запуска по расписанию или из CI (планировщик задач, cron, workflow)?",
)


def is_generated(path: str) -> bool:
    """Сгенерированный или декомпилированный код: кандидатов в нём не ищем."""
    lowered = path.lower()
    return "decompiled" in lowered or lowered.endswith((".g.cs", ".designer.cs"))


def is_test_path(path: str) -> bool:
    return bool(
        re.search(
            r"(^|/)(tests?/|test_|[^/]*Tests?\.cs$|[^/]*\.Tests?\.ps1$|[^/]*\.(test|spec)\.)", path
        )
    )


def identifier_counts(project: Path, rel: list[str]) -> Counter[str]:
    """Сколько раз каждое имя встречается во всех текстах проекта (код, настройки, документы)."""
    counts: Counter[str] = Counter()
    for path in rel:
        if Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        file = project / path
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts.update(IDENT.findall(text))
        counts.update(PS_NAME.findall(text))
    return counts


def python_definitions(path: str, text: str) -> list[tuple[str, str, int]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.decorator_list
        ):
            kind = "класс" if isinstance(node, ast.ClassDef) else "функция"
            found.append((node.name, kind, node.lineno))
    return found


def declarations(path: str, text: str) -> list[tuple[str, str, int]]:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return python_definitions(path, text)
    found: list[tuple[str, str, int]] = []
    if suffix == ".cs":
        for pattern, kind in ((CS_DECL, "тип"), (CS_METHOD, "метод")):
            for match in pattern.finditer(text):
                found.append((match[1], kind, text.count(chr(10), 0, match.start()) + 1))
    elif suffix in {".ps1", ".psm1"}:
        for match in PS_FUNCTION.finditer(text):
            found.append(
                (match[1], "функция PowerShell", text.count(chr(10), 0, match.start()) + 1)
            )
    return found


def candidates(project: Path, rel: list[str]) -> dict[str, Any]:
    """Публичные имена, которые в проекте встречаются один раз (только в объявлении)."""
    counts = identifier_counts(project, rel)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in rel:
        if (
            is_test_path(path)
            or is_generated(path)
            or Path(path).suffix.lower() not in {".py", ".cs", ".ps1", ".psm1"}
        ):
            continue
        try:
            text = (project / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, kind, line in declarations(path, text):
            if (
                name.startswith("_")
                or name in SKIP_NAMES
                or len(name) < 3
                or name.startswith(("test", "Test"))
            ):
                continue
            if counts[name] <= 1 and (path, name) not in seen:
                seen.add((path, name))
                rows.append({"name": name, "kind": kind, "path": path, "line": line})
    rows.sort(key=lambda r: (r["path"], r["line"]))
    return {"total": len(rows), "shown": rows[:MAX_CANDIDATES]}


def questions_markdown(base: list[dict[str, str]], dead: dict[str, Any]) -> str:
    """QUESTIONS.md для parch-analysis/: вопросы карточки и вопросы по мёртвому коду."""
    lines = [
        "# Вопросы владельцу (черновик анализа)",
        "",
        "Ответы пишет владелец; анализ ничего не угадывает и ничего не удаляет.",
        "",
        "## Вопросы по процессу",
        "",
        "| № | Вопрос | Ответ владельца |",
        "|---|---|---|",
    ]
    for item in base:
        lines.append(f"| {item['id']} | {item['question']} | |")
    lines += [
        "",
        "## Мёртвый код: кандидаты",
        "",
        f"Кандидатов: {dead['total']} (показано {len(dead['shown'])}). Имя встречается в проекте один раз, в объявлении. "
        "По каждому три вопроса: "
        + " ".join(f"({n}) {q}" for n, q in enumerate(QUESTIONS, start=1)),
        "",
        "| Кандидат | Где | Вызов снаружи? | Рефлексия? | Расписание? | Решение (удалить / оставить keep-until) |",
        "|---|---|---|---|---|---|",
    ]
    for row in dead["shown"]:
        lines.append(f"| `{row['name']}` ({row['kind']}) | {row['path']}:{row['line']} | | | | |")
    return chr(10).join(lines) + chr(10)
