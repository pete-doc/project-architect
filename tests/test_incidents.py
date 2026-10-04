# ruff: noqa: E501
"""F13, PR 1: отчёты об инцидентах, бюджет блока, статус «застрял» и карточка решения.

Спецификация: docs/specs/F13-incidents-stuck.md (STANDARD.md, 6.3, 6.5a, 6.6).
На каждое правило есть заведомо плохой пример, который проверка обязана остановить.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import test_standard_check as std
from test_status import GOAL_TEXT, board, feature, make, section

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "specs" / "F13-incidents-stuck.md"
TEMPLATE = REPO / "plugin" / "templates" / "docs" / "INCIDENT_TEMPLATE.md"

IMPACT = "- Критерий цели: G1\n- Обходной путь: нет\n- Потрачено: инцидентов 1 (Сессий: 2)\n"
HISTORY = (
    "- Поиск по коду (какие запросы): «retry_loop», `parse_report`: не нашёл\n"
    "- История изменений кода (`git log -S`): `git log -S retry_loop`: не нашёл\n"
)


def report(impact: str | None = IMPACT, history: str | None = HISTORY, block: str = "F4") -> str:
    text = f"# Отчёт об инциденте\n\nБлок: {block}\n\n## Что случилось\n\nУпал тест.\n\n"
    if impact is not None:
        text += f"## Влияние на цель\n\n{impact}\n"
    if history is not None:
        text += f"## Где искал\n\n{history}"
    return text


def workspace(
    root: Path, *files: tuple[str, str], status: str = "in_progress", extra: str = ""
) -> Path:
    std.project(root)
    (root / "state" / "incidents").mkdir(parents=True, exist_ok=True)
    features = [{"id": "F4", "status": status, "goal": ["G1"]}]
    if extra:
        features[0].update(json.loads(extra))
    (root / "state" / "features.json").write_text(
        json.dumps({"version": 1, "features": features}), encoding="utf-8"
    )
    for name, text in files:
        (root / "state" / "incidents" / name).write_text(text, encoding="utf-8")
    return root


GOOD_NAME = "2026-10-04-F4-loop.md"


# ---------- отчёт: имя, блок, разделы ----------


def test_a_complete_report_passes_the_standard(tmp_path: Path) -> None:
    std.passes(workspace(tmp_path, (GOOD_NAME, report())))


def test_the_impact_section_is_required_and_must_name_a_goal_or_say_none(tmp_path: Path) -> None:
    std.fails(workspace(tmp_path / "a", (GOOD_NAME, report(impact=None))), "Влияние на цель")
    formal = report(impact="- Обходной путь: нет\n- Потрачено: много\n")
    std.fails(workspace(tmp_path / "b", (GOOD_NAME, formal)), "Влияние на цель", GOOD_NAME)
    std.passes(workspace(tmp_path / "c", (GOOD_NAME, report(impact="Ни один критерий.\n"))))


@pytest.mark.parametrize(
    "history",
    [
        None,  # раздела нет
        "\n",  # пустой
        # метки шаблона без ответов
        "- Поиск по коду (какие запросы):\n- Реестр модулей и каталог возможностей (если есть):\n"
        "- История изменений кода (`git log -S`):\n- Прошлые отчёты и уроки:\n",
        "- Поиск по коду: ничего похожего не нашёл\n- Прошлые отчёты и уроки: тоже нет\n",  # без запросов
        "- Поиск по коду: «retry_loop»\n",  # один источник
        "- История изменений кода (`git log -S`): `git log -S retry`\n"
        "- Реестр модулей и каталог возможностей: «retry»\n",  # нет поиска по коду
        "- Поиск по коду: нашёл много\n- Прошлые отчёты и уроки: 2026-10-01-F4-loop.md\n",  # код без запроса
        "<!-- - Поиск по коду: «retry» -->\n<!-- - Реестр модулей: «retry» -->\n",  # спрятано в комментарий
    ],
)
def test_an_empty_history_section_fails_the_standard(tmp_path: Path, history: str | None) -> None:
    out = std.fails(workspace(tmp_path, (GOOD_NAME, report(history=history))), "Где искал")
    assert GOOD_NAME in out and "поиск по коду" in out


@pytest.mark.parametrize(
    "history",
    [
        "- Поиск по коду: «retry»\n- Реестр модулей и каталог возможностей: «retry», «Retry»: не нашёл\n",
        "- Поиск по коду: `retry_loop`\n- Прошлые отчёты и уроки: 2026-10-01-F4-loop.md: та же ошибка\n",
        "- Поиск по коду: «retry»\n- Прошлые отчёты и уроки: PR #16 чинил то же\n",
        "- Поиск по коду: «retry»\n- История изменений кода (`git log -S`): коммит 3ff0a4c менял файл\n",
        '- Поиск по коду: "retry"\n- История изменений кода (`git log -S`): `git log -S retry`: пусто\n',
    ],
)
def test_a_history_with_a_link_or_a_recorded_search_passes(tmp_path: Path, history: str) -> None:
    std.passes(workspace(tmp_path, (GOOD_NAME, report(history=history))))


def test_the_unfilled_template_is_not_a_valid_report(tmp_path: Path) -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    out = std.fails(workspace(tmp_path, (GOOD_NAME, text)), "Влияние на цель", "Где искал")
    assert out.count(GOOD_NAME) >= 2  # оба раздела названы в одном и том же файле


def test_report_names_and_blocks_are_checked(tmp_path: Path) -> None:
    std.fails(workspace(tmp_path / "a", ("notes.md", report())), "имя должно быть")
    std.fails(workspace(tmp_path / "b", ("2026-10-04-F77-loop.md", report())), "блока F77 нет")
    std.passes(workspace(tmp_path / "c", ("2026-10-04-NONE-loop.md", report(block="NONE"))))
    std.passes(
        workspace(tmp_path / "d", ("README.md", "Как вести отчёты\n"), (GOOD_NAME, report()))
    )


# ---------- бюджет и статус stuck ----------


SECOND = ("2026-10-05-F4-blocker.md", report())


def test_a_block_that_used_its_budget_must_be_stuck(tmp_path: Path) -> None:
    out = std.fails(
        workspace(tmp_path / "a", (GOOD_NAME, report()), SECOND), "блока F4", "бюджете 2", "stuck"
    )
    assert "решение владельца" in out
    std.passes(workspace(tmp_path / "b", (GOOD_NAME, report()), SECOND, status="stuck"))
    std.passes(workspace(tmp_path / "c", (GOOD_NAME, report()), SECOND, status="done"))
    std.passes(workspace(tmp_path / "d", (GOOD_NAME, report()), SECOND, status="dropped"))
    std.passes(workspace(tmp_path / "e", (GOOD_NAME, report())))  # один инцидент из двух


def test_reports_outside_a_block_do_not_spend_a_block_budget(tmp_path: Path) -> None:
    none = [(f"2026-10-0{n}-NONE-loop.md", report(block="NONE")) for n in (4, 5, 6)]
    std.passes(workspace(tmp_path, *none))


def test_the_budget_comes_from_constitution_and_a_block_may_have_its_own(tmp_path: Path) -> None:
    constitution = tmp_path / "docs" / "CONSTITUTION.md"
    root = workspace(tmp_path / "a", (GOOD_NAME, report()), SECOND)
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "CONSTITUTION.md").write_text(
        "## Целевая ОС\n\nWindows 11\n\nБюджет инцидентов на блок: 3\n", encoding="utf-8"
    )
    std.passes(root)  # два из трёх
    third = ("2026-10-06-F4-loop.md", report())
    (root / "state" / "incidents" / third[0]).write_text(third[1], encoding="utf-8")
    std.fails(root, "бюджете 3")
    own = workspace(tmp_path / "b", (GOOD_NAME, report()), SECOND, extra='{"incident_budget": 5}')
    std.passes(own)  # у блока свой бюджет 5, решение владельца
    assert not constitution.exists()


# ---------- табло: карточка решения ----------


def stuck_project(tmp_path: Path, workaround: str = "выгружать в Word") -> Path:
    root = make(tmp_path, [feature("F9", ["G1"], [], "stuck", title="Выгрузка в PDF")])
    folder = root / "state" / "incidents"
    folder.mkdir()
    impact = f"- Критерий цели: G1\n- Обходной путь: {workaround}\n- Потрачено: Сессий: 2\n"
    (folder / "2026-10-14-F9-loop.md").write_text(report(block="F9"), encoding="utf-8")
    (folder / "2026-10-15-F9-blocker.md").write_text(
        report(impact=impact, block="F9"), encoding="utf-8"
    )
    return root


def test_a_stuck_block_gets_a_decision_card_with_three_answers(tmp_path: Path) -> None:
    decisions = section(board(stuck_project(tmp_path)), "Нужно ваше решение")
    assert "- **F9 «Выгрузка в PDF»** застрял: 2 инцидента, 4 сессии" in decisions
    assert "Нужен для: G1 «Заказ считается верно»." in decisions
    assert "Обходной путь (из отчёта 2026-10-15-F9-blocker.md): выгружать в Word" in decisions
    assert "1. Отказаться" in decisions and "2. Обходной путь" in decisions
    assert "3. Продолжать" in decisions and "бюджет блока поднимается на 1" in decisions


def test_the_card_says_when_the_report_names_no_workaround(tmp_path: Path) -> None:
    decisions = section(
        board(stuck_project(tmp_path / "x", workaround="нет")), "Нужно ваше решение"
    )
    assert "Обходной путь (из отчёта 2026-10-15-F9-blocker.md): в отчёте не указан" in decisions


def test_the_board_flags_a_block_that_spent_its_budget_but_is_not_stuck(tmp_path: Path) -> None:
    root = make(tmp_path, [feature("F9", ["G1"], [], "in_progress")])
    folder = root / "state" / "incidents"
    folder.mkdir()
    for name in ("2026-10-14-F9-loop.md", "2026-10-15-F9-blocker.md"):
        (folder / name).write_text(report(block="F9"), encoding="utf-8")
    problems = section(board(root), "Замечания к плану")
    assert "F9: инцидентов 2 при бюджете 2, а статус не «застрял»" in problems


def load_status() -> ModuleType:
    path = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_status.py"
    spec = importlib.util.spec_from_file_location("parch_status_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # без этого dataclass в модуле не находит свой модуль
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("number", "word"),
    [(1, "инцидент"), (2, "инцидента"), (4, "инцидента"), (5, "инцидентов"), (11, "инцидентов"),
     (21, "инцидент"), (22, "инцидента"), (111, "инцидентов")],
)  # fmt: skip
def test_russian_plural_forms_of_the_card(number: int, word: str) -> None:
    assert load_status().plural(number, ("инцидент", "инцидента", "инцидентов")) == word


# ---------- шаблоны, спецификация, правила ----------


def test_init_copies_the_incident_template_and_the_budget_line() -> None:
    constitution = (REPO / "plugin" / "templates" / "docs" / "CONSTITUTION.md").read_text(
        encoding="utf-8"
    )
    assert "\nБюджет инцидентов на блок: 2\n" in constitution
    init = (REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py").read_text(
        encoding="utf-8"
    )
    assert "docs/INCIDENT_TEMPLATE.md" in init
    template = TEMPLATE.read_text(encoding="utf-8")
    for heading in ("## Влияние на цель", "## Где искал", "NONE", "Поиск по коду"):
        assert heading in template, heading


def test_the_f13_spec_is_approved_and_records_the_owner_amendments() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert "Статус: утверждена владельцем" in spec
    for fact in (
        "Где искал",
        "вне блока",
        "новой сессией",
        "F13-…",
        "поиск по коду с запросом",
    ):
        assert fact in spec, fact


def test_agents_md_names_branches_after_blocks() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "начинай с идентификатора блока" in agents and "F13-" in agents


def test_goal_text_helper_still_has_the_criterion_the_card_uses() -> None:
    assert "- **G1.** Заказ считается верно" in GOAL_TEXT
