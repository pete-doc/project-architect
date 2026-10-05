# ruff: noqa: E501
"""F15, PR 4г: шаблон описания PR с разделом «Основания» и его копирование в проект при подключении (init-project).

Требования к шаблону собраны в одну функцию `template_problems`; её применяют и к шаблону продукта, и к копии в проекте.
Плохие примеры этой функции: нет раздела «Основания», он стоит не там, из подсказки пропал источник протокола. Саму
проверку заполнения «Оснований» в PR подключит блок F14 (CI), здесь проверяется только шаблон и его копирование.
"""

import re
from pathlib import Path

from test_skills import init

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "plugin" / "templates" / "github" / "pull_request_template.md"
HEADINGS = [
    "Что сделано",
    "Какие решения приняты и почему",
    "Основания",
    "Как проверить, что работает",
]
SOURCES = (
    "CAPABILITIES.md",
    "MODULES.md",
    "git log -S",
    "docs/adr/",
    "LESSONS.md",
    "state/incidents/",
)


def template_problems(text: str) -> list[str]:
    """Что не так с шаблоном описания PR: разделы, порядок, источники в подсказке «Оснований»."""
    problems: list[str] = []
    found = re.findall(r"^## (.+)$", text, re.MULTILINE)
    if "Основания" not in found:
        problems.append("нет раздела «Основания»")
    elif found != HEADINGS:
        problems.append(f"разделы или их порядок не те: {found}")
    if "## Основания" in text and "## Как проверить" in text:
        grounds = text.split("## Основания", 1)[1].split("## Как проверить", 1)[0]
        problems += [
            f"в подсказке «Оснований» нет источника {s}" for s in SOURCES if s not in grounds
        ]
    return problems


def test_the_product_template_has_no_problems() -> None:
    assert template_problems(TEMPLATE.read_text(encoding="utf-8")) == []


def test_a_template_without_the_grounds_section_is_rejected() -> None:
    broken = TEMPLATE.read_text(encoding="utf-8").replace("## Основания", "## Источники")
    assert "нет раздела «Основания»" in template_problems(broken)


def test_a_template_with_the_grounds_section_in_the_wrong_place_is_rejected() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    grounds = text.split("## Основания", 1)[1].split("## Как проверить", 1)[0]
    moved = text.replace("## Основания" + grounds, "") + "\n## Основания" + grounds
    assert any("порядок" in p for p in template_problems(moved))


def test_a_template_whose_hint_lost_a_protocol_source_is_rejected() -> None:
    broken = TEMPLATE.read_text(encoding="utf-8").replace("`git log -S", "`git log")
    assert any("git log -S" in p for p in template_problems(broken))


def test_init_copies_the_same_template_into_the_project(tmp_path: Path) -> None:
    init(tmp_path)
    copied = tmp_path / ".github" / "pull_request_template.md"
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")
    assert template_problems(copied.read_text(encoding="utf-8")) == []
