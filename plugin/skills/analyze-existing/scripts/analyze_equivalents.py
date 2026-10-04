# ruff: noqa: E501
"""Равноценные файлы (docs/specs/F15-analyze-existing.md): файл проекта с тем же назначением, что у файла стандарта,
засчитывается в карточку без штрафа, а план приведения лишь предлагает привести имя к имени стандарта.

Словарь начинается с известных соответствий; всё неизвестное уходит вопросом владельцу, а не угадывается.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple


class Role(NamedTuple):
    key: str  # роль в проекте
    title: str  # что это
    standard: str  # имя по стандарту (папка кончается на /)
    aliases: tuple[str, ...]  # равноценные пути (папка кончается на /), по убыванию приоритета
    action: str  # что предлагает план: «Переименовать» (тот же формат) или «Перестроить» (другой формат)
    note: str


ROLES: tuple[Role, ...] = (
    Role("goal", "цель продукта", "docs/GOAL.md",
         ("docs/GOAL.md", "GOAL.md", "master_plan.md", "docs/master_plan.md", "VISION.md", "docs/VISION.md"),
         "Переименовать", "критерии вида G1 владелец и планировщик формулируют отдельно"),
    Role("decisions", "журнал решений", "docs/adr/",
         ("docs/adr/", "docs/decisions/", "DECISIONS.md", "docs/DECISIONS.md"),
         "Переименовать", "формат «дата — решение — почему» сохраняется, записи становятся отдельными ADR"),
    Role("status", "текущее состояние", "state/STATUS.md",
         ("state/STATUS.md", "STATUS.md"),
         "Переименовать", "в стандарте табло STATUS.md строится из реестра блоков"),
    Role("plan", "план работ", "state/features.json",
         ("state/features.json", "BACKLOG.md", "ROADMAP.md", "docs/BACKLOG.md"),
         "Перестроить", "формат другой: реестр блоков с целью, зависимостями и тестами приёмки заводится отдельно"),
    Role("incidents", "отчёты о сбоях", "state/incidents/",
         ("state/incidents/", "docs/incidents/", "incidents/"),
         "Переименовать", "имя файла ДАТА-БЛОК-слово.md и разделы отчёта по шаблону"),
    Role("lessons", "уроки", "docs/LESSONS.md",
         ("docs/LESSONS.md", "LESSONS.md"),
         "Переименовать", "каждый урок со ссылкой на тест"),
    Role("modules", "карта модулей", "docs/MODULES.md",
         ("docs/MODULES.md", "MODULES.md", "docs/architecture.md", "ARCHITECTURE.md"),
         "Переименовать", "таблица: модуль, путь, назначение, язык, статус"),
    Role("interfaces", "межъязыковые швы", "docs/INTERFACES.md",
         ("docs/INTERFACES.md", "INTERFACES.md"),
         "Переименовать", "по записи на каждый шов с контрактным тестом"),
    Role("questions", "вопросы владельцу", "docs/QUESTIONS.md",
         ("docs/QUESTIONS.md", "QUESTIONS.md"),
         "Переименовать", "таблица вопросов с колонкой ответа владельца"),
)  # fmt: skip


def exists(project: Path, alias: str) -> bool:
    path = project / alias
    if alias.endswith("/"):
        return path.is_dir() and any(path.iterdir())
    return path.is_file()


def find_equivalents(project: Path) -> list[dict[str, Any]]:
    """По строке на каждую роль, которая в проекте есть: где лежит и совпадает ли имя со стандартом."""
    found: list[dict[str, Any]] = []
    for role in ROLES:
        path = next((alias for alias in role.aliases if exists(project, alias)), None)
        if path is None:
            continue
        found.append(
            {
                "role": role.key,
                "title": role.title,
                "path": path,
                "standard": role.standard,
                "is_standard": path == role.standard,
                "action": role.action,
                "note": role.note,
            }
        )
    return found


def equivalent_path(equivalents: list[dict[str, Any]], role: str) -> str | None:
    for row in equivalents:
        if row["role"] == role:
            return str(row["path"])
    return None
