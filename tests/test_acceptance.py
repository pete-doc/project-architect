"""Приёмка владельцем (ADR-0014): блок без тестов становится «готово» только по вашему «принято».

Файл `state/acceptance/<блок>.md` с отмеченными критериями и строкой итога должен быть слит в main.
"""

from pathlib import Path

import pytest
from test_status import PASS_A, REPO, board, feature, junit, make, section

BOXES_DONE = "- [x] Отчёт понятен\n- [x] Цель утверждена\n"
VERDICT = "Итог: принято владельцем, 2026-10-04\n"
PENDING = "Итог: ожидает приёмки\n"


def accept(root: Path, block: str, text: str) -> None:
    folder = root / "state" / "acceptance"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{block}.md").write_text(f"# Приёмка {block}\n\n{text}", encoding="utf-8")


def test_a_block_without_tests_becomes_done_only_after_the_owner_accepted_it(
    tmp_path: Path,
) -> None:
    root = make(tmp_path, [feature("F1", ["G1"], [], "waiting_owner", title="Пилот")])
    assert "| ✅ готово |" not in board(root) and "ждёт владельца" in board(root)  # файла нет
    accept(root, "F1", BOXES_DONE + PENDING)
    text = board(root)
    assert "| ✅ готово |" not in text
    assert "**F1 «Пилот»** ждёт владельца, приёмка в `state/acceptance/F1.md`" in text
    accept(root, "F1", BOXES_DONE + VERDICT)
    accepted = board(root)
    assert "| ✅ готово |" in accepted and "критерий G1 выполнен" in accepted
    assert "ничего: всё идёт без вашего участия" in section(accepted, "Нужно ваше решение")


@pytest.mark.parametrize(
    "text",
    [
        "- [x] Отчёт понятен\n- [ ] Цель утверждена\n" + VERDICT,  # не все критерии отмечены
        VERDICT,  # критериев нет вовсе
        BOXES_DONE + "Итог: принято\n",  # без слов «владельцем» и даты
        BOXES_DONE + "Итог: принято владельцем, ДАТА\n",  # подсказка из шаблона, не дата
    ],
)
def test_an_incomplete_acceptance_file_does_not_make_the_block_done(
    tmp_path: Path, text: str
) -> None:
    root = make(tmp_path, [feature("F1", ["G1"], [], "waiting_owner")])
    accept(root, "F1", text)
    assert "| ✅ готово |" not in board(root)


def test_a_block_with_tests_and_an_acceptance_file_needs_both(tmp_path: Path) -> None:
    features = [feature("F1", ["G1"], ["tests/test_a.py"], "in_progress")]
    root = make(tmp_path, features, junit(PASS_A))
    accept(root, "F1", BOXES_DONE + PENDING)
    assert "| ✅ готово |" not in board(root)  # тесты прошли, владелец ещё не принял
    accept(root, "F1", BOXES_DONE + VERDICT)
    assert "| ✅ готово |" in board(root)
    failing = make(tmp_path / "f", features, junit(("tests.test_a", "test_one", "failed")))
    accept(failing, "F1", BOXES_DONE + VERDICT)
    assert "| ✅ готово |" not in board(failing)  # владелец принял, но тесты упали


def test_a_manual_done_with_a_pending_acceptance_is_flagged(tmp_path: Path) -> None:
    root = make(tmp_path, [feature("F1", ["G1"], [], "done")])
    accept(root, "F1", BOXES_DONE + PENDING)
    problems = section(board(root), "Замечания к плану")
    assert (
        "F1: помечен «готово», но приёмка владельцем не оформлена (state/acceptance/)" in problems
    )


def test_the_product_pilot_block_waits_for_the_owner_acceptance_file() -> None:
    """Имя прежнее (храповик не даёт переименовать); с 2026-10-05 проверяется принятое."""
    from parch_status import acceptance_state

    assert acceptance_state(REPO, "F17") == "accepted"
    text = board(REPO, report=False)
    assert "F17 «Пилот на реальном проекте»" in text  # блок на табло
    assert "приёмка в `state/acceptance/F17.md`" not in text  # приёмка оформлена, не ждёт
    file = (REPO / "state" / "acceptance" / "F17.md").read_text(encoding="utf-8")
    assert file.count("- [x]") == 5 and "- [ ]" not in file
    assert "Итог: принято владельцем, 2026-10-05" in file
    for criterion in (
        "doctor",
        "analyze-existing",
        "без чтения кода",
        "решил, как дальше",
        "записаны",
    ):
        assert criterion in file, criterion


def test_agents_md_makes_acceptance_changes_wait_for_the_owner_and_adr_0014_exists() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "`state/acceptance/` (приёмка владельцем, ADR-0014)" in agents
    adr = (REPO / "docs" / "adr" / "0014-priemka-vladeltsem.md").read_text(encoding="utf-8")
    for fact in ("state/acceptance/", "Итог: принято владельцем", "все критерии", "«сливай»"):
        assert fact in adr, fact
