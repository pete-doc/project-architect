# ruff: noqa: E501
"""Каталог возможностей проекта и обязательные описания публичных функций (F15, PR 3).

Каталог `docs/CAPABILITIES.md` собирается автоматически из однострочных описаний публичных функций (Python: первая
строка docstring; C#: первая строка `<summary>`). Проверка падает, если у публичной функции нет описания или каталог
не совпадает с кодом (отстал). `--update` пересобирает каталог. Описания пишет человек или ИИ, каталог руками не правят.
Языки: Python и C#; TypeScript и PowerShell следующим шагом.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

CATALOG_PATH = "docs/CAPABILITIES.md"
DEBT_PATH = "state/catalog-baseline.json"
MAX_DESCRIPTION = 120
SKIP_DIRS = {
    ".git", ".github", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages", "build",
    "dist", "bin", "obj", "archive", "tests", "test", "docs", ".tox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache",
}  # fmt: skip
HEADER = (
    "# Каталог возможностей\n\n"
    "Собран автоматически из однострочных описаний публичных функций. Руками не правится: пересобрать командой "
    "`python .github/parch/parch_ci.py catalog --update`. Перед тем как писать новую функцию, найдите похожую здесь.\n"
)


@dataclass(frozen=True)
class Entry:
    language: str
    module: str
    name: str
    description: str
    where: str


@dataclass
class Frame:
    """Тип C#, внутри которого идёт разбор: имя, публичный ли, глубина скобок его тела."""

    name: str
    public: bool
    body_depth: int
    entered: bool = False


@dataclass(frozen=True)
class Missing:
    where: str
    name: str
    reason: str


def source_files(project: Path, suffix: str) -> list[Path]:
    """Файлы кода проекта без тестов, сборочных и служебных папок (parch-analysis*, tests/projects тоже)."""
    found: list[Path] = []
    for path in sorted(project.rglob(f"*{suffix}")):
        parts = path.relative_to(project).parts
        if any(p in SKIP_DIRS or p.startswith("parch-analysis") for p in parts[:-1]):
            continue
        if any(p.endswith(".Tests") or p.endswith(".tests") for p in parts[:-1]):
            continue
        found.append(path)
    return found


def relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


# ---------- Python ----------


def py_is_test(path: Path) -> bool:
    return (
        path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name in {"conftest.py", "setup.py"}
    )


def py_description(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    doc = ast.get_docstring(node)
    return (doc or "").strip().splitlines()[0].strip() if (doc or "").strip() else ""


def py_is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "overload")
        or (isinstance(d, ast.Attribute) and d.attr == "overload")
        for d in node.decorator_list
    )


def py_entries(project: Path) -> tuple[list[Entry], list[Missing]]:
    entries: list[Entry] = []
    missing: list[Missing] = []
    for path in source_files(project, ".py"):
        if py_is_test(path) or (path.name.startswith("_") and path.name != "__init__.py"):
            continue
        rel = relative(project, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        funcs: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((node.name, node))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                funcs += [
                    (f"{node.name}.{sub.name}", sub)
                    for sub in node.body
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
        for name, node in funcs:
            if node.name.startswith("_") or py_is_overload(node):
                continue
            where = f"{rel}:{node.lineno}"
            description = py_description(node)
            if not description:
                missing.append(
                    Missing(where, name, "нет описания (docstring с одной строкой сути)")
                )
            elif len(description) > MAX_DESCRIPTION:
                missing.append(
                    Missing(where, name, f"первая строка описания длиннее {MAX_DESCRIPTION} знаков")
                )
            else:
                entries.append(Entry("python", rel, name, description, where))
    return entries, missing


# ---------- C# ----------

CS_TYPE = re.compile(r"\b(class|struct|record|interface)\s+(\w+)")
CS_PUBLIC_METHOD = re.compile(
    r"^public\s+(?:(?:static|async|virtual|override|sealed|abstract|partial|unsafe|extern|new)\s+)*"
    r"(?!class\b|struct\b|record\b|interface\b|enum\b|delegate\b|event\b|const\b|operator\b)"
    r"[\w<>\[\],.?()\s]+?\s+(\w+)\s*(?:<[^>()]*>)?\s*\("
)
CS_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)


def cs_doc_block(lines: list[str], index: int) -> str:
    """Комментарий `///` над членом (атрибуты `[...]` между ними пропускаются)."""
    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].strip().startswith("["):
        cursor -= 1
    while cursor >= 0 and lines[cursor].strip().startswith("///"):
        block.append(lines[cursor].strip()[3:].strip())
        cursor -= 1
    return " ".join(reversed(block))


def cs_description(block: str) -> str:
    found = CS_SUMMARY.search(block)
    text = re.sub(r"<[^>]+>", "", found.group(1)).strip() if found else ""
    return re.sub(r"\s+", " ", text)


def cs_entries(project: Path) -> tuple[list[Entry], list[Missing]]:
    entries: list[Entry] = []
    missing: list[Missing] = []
    for path in source_files(project, ".cs"):
        rel = relative(project, path)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        depth = 0
        stack: list[Frame] = []
        for index, raw in enumerate(lines):
            line = raw.strip()
            if line.startswith("//"):
                continue
            declared = CS_TYPE.search(line)
            if declared and "(" not in line.split(declared.group(0))[0]:
                public = bool(re.match(r"(?:\[[^\]]*\]\s*)*public\b", line))
                stack.append(Frame(declared.group(2), public, depth + 1))
            method = CS_PUBLIC_METHOD.match(line)
            if method and stack and all(item.public for item in stack):
                name = f"{stack[-1].name}.{method.group(1)}"
                block = cs_doc_block(lines, index)
                where = f"{rel}:{index + 1}"
                description = cs_description(block)
                if "<inheritdoc" in block:
                    pass  # описание наследуется
                elif not description:
                    missing.append(
                        Missing(where, name, "нет описания (`/// <summary>` с одной строкой сути)")
                    )
                elif len(description) > MAX_DESCRIPTION:
                    missing.append(
                        Missing(where, name, f"описание длиннее {MAX_DESCRIPTION} знаков")
                    )
                else:
                    entries.append(Entry("csharp", rel, name, description, where))
            depth += line.count("{") - line.count("}")
            while stack:
                top = stack[-1]
                if depth >= top.body_depth:
                    top.entered = True
                    break
                if not top.entered:
                    break  # тело типа ещё не открыто: скобка на следующей строке
                stack.pop()
    return entries, missing


# ---------- каталог ----------


def render(entries: list[Entry]) -> str:
    """Текст каталога: стабильный порядок, чтобы повторная сборка давала тот же файл."""
    lines = [HEADER]
    for language, title in (("python", "Python"), ("csharp", "C#")):
        mine = sorted(
            (e for e in entries if e.language == language), key=lambda e: (e.module, e.name)
        )
        if not mine:
            continue
        lines += [f"## {title}", "", "| Модуль | Функция | Описание |", "|---|---|---|"]
        for e in mine:
            lines.append(f"| `{e.module}` | `{e.name}` | {e.description.replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect(project: Path) -> tuple[list[Entry], list[Missing]]:
    py_found, py_missing = py_entries(project)
    cs_found, cs_missing = cs_entries(project)
    return py_found + cs_found, py_missing + cs_missing


def debt_key(item: Missing) -> str:
    """Опознавательный знак функции без описания: файл и имя, без номера строки (строки сдвигаются)."""
    return f"{item.where.rsplit(':', 1)[0]}:{item.name}"


def read_debt(project: Path) -> set[str] | None:
    """Функции без описания, записанные владельцем (храповик); None, если файла нет."""
    path = project / DEBT_PATH
    if not path.is_file():
        return None
    data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    raw = cast("dict[str, object]", data).get("undescribed") if isinstance(data, dict) else None
    return {str(x) for x in cast("list[object]", raw)} if isinstance(raw, list) else set()


def write_debt(project: Path, debt: set[str]) -> None:
    path = project / DEBT_PATH
    if not debt:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": 1, "undescribed": sorted(debt)}, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def check(
    project: Path, update: bool = False, accept_new: bool = False
) -> tuple[list[str], list[str]]:
    """(провалы, пометки): новая функция без описания и отставший каталог падают; старый долг из baseline допускается.

    Храповик `state/catalog-baseline.json`: функции без описания, которые были в проекте до подключения проверки,
    записаны там по файлу и имени; число может только снижаться. `update` пересобирает каталог и сокращает baseline
    по исправленным функциям; записать новый долг в baseline (или создать его) может только владелец: `accept_new`.
    """
    entries, missing = collect(project)
    problems: list[str] = []
    notes: list[str] = []
    known = read_debt(project)
    current = {debt_key(m): m for m in missing}
    fresh = sorted(k for k in current if known is None or k not in known)
    if fresh and not accept_new:
        problems.append(
            "У новых публичных функций нет однострочного описания (оно попадает в каталог возможностей):"
        )
        problems += [
            f"  {current[k].where}: {current[k].name}: {current[k].reason}" for k in fresh[:30]
        ]
        if len(fresh) > 30:
            problems.append(f"  ... и ещё {len(fresh) - 30}")
    if update and not problems:
        new_debt = set(current) if accept_new else set(current) & (known or set())
        if new_debt != (known or set()):
            write_debt(project, new_debt)
            notes.append(f"Долг по описаниям записан: функций без описания {len(new_debt)}.")
    elif known is not None and not problems:
        paid = sorted(known - set(current))
        if paid:
            notes.append(
                f"Описаны {len(paid)} функций из долга: сократите baseline командой catalog --update."
            )
    wanted = render(entries) if entries else ""
    path = project / CATALOG_PATH
    on_disk = path.read_text(encoding="utf-8") if path.is_file() else ""
    if update and not problems:
        if wanted != on_disk and (wanted or path.is_file()):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(wanted, encoding="utf-8", newline="\n")
            notes.append(f"Каталог пересобран: {CATALOG_PATH}, функций {len(entries)}.")
    elif wanted != on_disk and not problems:
        problems.append(
            f"Каталог возможностей {CATALOG_PATH} отстал от кода или отсутствует. Пересоберите: "
            "python .github/parch/parch_ci.py catalog --update"
        )
    if not problems:
        old = len(current)
        tail = (
            f", в долге без описания {old} (число только снижается)" if old else ", все с описанием"
        )
        notes.append(f"Каталог возможностей в порядке: функций {len(entries)}{tail}.")
    return problems, notes
