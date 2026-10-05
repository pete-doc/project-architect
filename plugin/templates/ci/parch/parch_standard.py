# ruff: noqa: E501
"""Правила состава проекта для проверки `standard` (F14, PR 1; STANDARD.md, раздел 11).

Три правила: обязательные файлы на месте; нет конкурирующих файлов инструкций для ИИ; нет `.md` вне разрешённых мест.
Применяются только к подключённому проекту: у него установлен `.github/parch/parch_ci.py` (в репозитории самого продукта
скрипт лежит в `plugin/templates/`, поэтому правила его не касаются).

Строгость (решение владельца, 2026-10-05): на новом проекте нарушение это провал. Проект, записанный как существующий
(`existing_project: true` в `state/baseline.json`, его ставит init-project, если при подключении уже был код), сначала только
получает предупреждение. Когда владелец запишет долг (`standard --update --accept-new`, файл `state/standard-baseline.json`),
включается храповик: записанные нарушения допускаются, число может только снижаться, новое нарушение падает.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import cast

DEBT_FILE = "state/standard-baseline.json"
REQUIRED = (
    ("цель продукта", ("docs/GOAL.md", "GOAL.md")),
    ("конституция проекта", ("docs/CONSTITUTION.md", "CONSTITUTION.md")),
    ("карта модулей", ("docs/MODULES.md",)),
    ("реестр блоков", ("state/features.json",)),
    ("шаблон отчёта об инциденте", ("docs/INCIDENT_TEMPLATE.md",)),
)
COMPETING = (
    ".cursorrules", ".windsurfrules", ".clinerules", "GEMINI.md", ".github/copilot-instructions.md",
)  # fmt: skip
COMPETING_DIRS = (".cursor/rules",)
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "bin", "obj", "dist", "build",
    "site-packages", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
}  # fmt: skip
ROOT_MD = {
    "agents.md",
    "claude.md",
    "readme.md",
    "goal.md",
    "constitution.md",
}  # две последних допустимы вместо docs/
ALLOWED_TOP = {"docs", "state", "analysis", ".github", ".claude"}


def is_managed(project: Path) -> bool:
    """Подключённый проект: у него установлен скрипт проверок `.github/parch/parch_ci.py`."""
    return (project / ".github" / "parch" / "parch_ci.py").is_file()


def exists_any(project: Path, options: tuple[str, ...]) -> bool:
    return any((project / o).is_file() for o in options)


def file_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for title, options in REQUIRED:
        if not exists_any(project, options):
            found[f"file:{options[0]}"] = (
                f"нет файла «{title}» ({' или '.join(options)}): создайте его по шаблону продукта "
                "(init-project кладёт шаблоны)"
            )
    return found


def instruction_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in COMPETING:
        if (project / name).is_file():
            found[f"instructions:{name}"] = (
                f"{name}: второй файл инструкций для ИИ рядом с AGENTS.md; оставьте одно место (AGENTS.md)"
            )
    for folder in COMPETING_DIRS:
        base = project / folder
        if base.is_dir():
            for path in sorted(p for p in base.rglob("*") if p.is_file()):
                rel = path.relative_to(project).as_posix()
                found[f"instructions:{rel}"] = (
                    f"{rel}: правила для ИИ вне AGENTS.md; перенесите их в AGENTS.md"
                )
    claude, agents = project / "CLAUDE.md", project / "AGENTS.md"
    if claude.is_file() and agents.is_file():
        text = claude.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"@AGENTS\.md|AGENTS\.md", text):
            found["instructions:CLAUDE.md"] = (
                "CLAUDE.md не ссылается на AGENTS.md: две разные инструкции расходятся; "
                "оставьте в CLAUDE.md строку `@AGENTS.md` и правила Claude Code"
            )
    return found


def markdown_allowed(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1].lower()
    if len(parts) == 1:
        return name in ROOT_MD
    if parts[0].lower().startswith("parch-analysis") or parts[0] in ALLOWED_TOP:
        return True
    if name == "skill.md" or "agents" in parts[:-1]:
        return True
    return "docs" in parts[:-1]


def markdown_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(project.rglob("*.md")):
        parts = path.relative_to(project).parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        rel = path.relative_to(project).as_posix()
        if not markdown_allowed(rel):
            found[f"md:{rel}"] = (
                f"{rel}: .md вне разрешённых мест (docs/, state/, корневые README, AGENTS, CLAUDE)"
            )
    return found


def read_debt(project: Path) -> set[str] | None:
    path = project / DEBT_FILE
    if not path.is_file():
        return None
    data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    raw = cast("dict[str, object]", data).get("violations") if isinstance(data, dict) else None
    return {str(x) for x in cast("list[object]", raw)} if isinstance(raw, list) else set()


def write_debt(project: Path, debt: set[str]) -> None:
    path = project / DEBT_FILE
    if not debt:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": 1, "violations": sorted(debt)}, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def existing_project(project: Path) -> bool:
    path = project / "state" / "baseline.json"
    if not path.is_file():
        return False
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return False
    return (
        isinstance(data, dict) and cast("dict[str, object]", data).get("existing_project") is True
    )


def check(
    project: Path, update: bool = False, accept_new: bool = False
) -> tuple[list[str], list[str]]:
    """(провалы, пометки): правила состава проекта в трёх режимах: новый проект, существующий без долга, с храповиком."""
    if not is_managed(project):
        return [], [
            "Правила состава проекта (файлы, инструкции, .md) не применяются: проект не подключён (нет .github/parch/)."
        ]
    found = {
        **file_violations(project),
        **instruction_violations(project),
        **markdown_violations(project),
    }
    debt = read_debt(project)
    problems: list[str] = []
    notes: list[str] = []
    recording = (
        update and accept_new
    )  # владелец записывает долг: предупреждение не нужно, долг пишется ниже
    if debt is None and existing_project(project) and not recording:
        if found:
            notes.append(
                f"Предупреждение (проект записан как существующий, долг по составу не записан): нарушений {len(found)}. "
                "Запишите долг командой standard --update --accept-new, дальше новые нарушения будут падать."
            )
            notes += [f"  {m}" for m in list(found.values())[:20]]
        return problems, notes
    fresh = sorted(k for k in found if debt is None or k not in debt)
    if fresh and not accept_new:
        problems.append("Состав проекта не соответствует стандарту:")
        problems += [f"  {found[k]}" for k in fresh[:30]]
        if len(fresh) > 30:
            problems.append(f"  ... и ещё {len(fresh) - 30}")
        return problems, notes
    if update:
        new_debt = set(found) if accept_new else set(found) & (debt or set())
        if new_debt != (debt or set()):
            write_debt(project, new_debt)
            notes.append(f"Долг по составу проекта записан: нарушений {len(new_debt)}.")
    elif debt is not None and debt - set(found):
        notes.append(
            f"Исправлено нарушений из долга: {len(debt - set(found))}; сократите baseline: standard --update."
        )
    if found:
        notes.append(f"Нарушений состава в долге: {len(found)} (число только снижается).")
    return problems, notes
