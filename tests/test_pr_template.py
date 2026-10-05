# ruff: noqa: E501
"""F15, PR 4г: шаблон описания PR с разделом «Основания» и его копирование в проект при подключении (init-project).

Плохие примеры: в шаблоне нет раздела «Основания» или он стоит не там; init не кладёт шаблон в `.github/`; шаблон в проекте
не совпадает с шаблоном продукта.
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


def headings(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, re.MULTILINE)


def test_the_template_has_the_grounds_section_between_decisions_and_checks() -> None:
    assert headings(TEMPLATE.read_text(encoding="utf-8")) == HEADINGS


def test_a_template_without_the_grounds_section_would_not_pass_the_same_check() -> None:
    broken = TEMPLATE.read_text(encoding="utf-8").replace("## Основания", "## Источники")
    assert headings(broken) != HEADINGS


def test_the_grounds_section_names_every_source_of_the_protocol() -> None:
    text = (
        TEMPLATE.read_text(encoding="utf-8")
        .split("## Основания", 1)[1]
        .split("## Как проверить", 1)[0]
    )
    for source in (
        "CAPABILITIES.md",
        "MODULES.md",
        "git log -S",
        "docs/adr/",
        "LESSONS.md",
        "state/incidents/",
    ):
        assert source in text, source


def test_init_copies_the_template_into_the_project(tmp_path: Path) -> None:
    init(tmp_path)
    copied = tmp_path / ".github" / "pull_request_template.md"
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")
    assert headings(copied.read_text(encoding="utf-8")) == HEADINGS
