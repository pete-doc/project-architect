"""Табло STATUS.md (STANDARD.md, 7.1): генератор, пересчёт после слияния, табло самого продукта.

Табло строится только из GOAL.md, features.json, отчёта тестов, incidents/, ADR и QUESTIONS.md.
Статус «готово» приходит из тестов приёмки блока, а не из слов агентов.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_status.py"
TEMPLATE_STATE = REPO / "plugin" / "templates" / "ci" / "state.yml"
GOAL_TEXT = (
    "# GOAL — цель продукта «Магазин»\n\n> **Статус: {status}.**\n\n"
    "## Критерии готовности продукта\n\n"
    "- **G1.** Заказ считается верно\n- **G2.** Заказ можно посмотреть\n- **G3.** Есть отчёты\n"
)
APPROVED = "утверждена владельцем, 2026-10-03"


def feature(
    id_: str,
    goal: list[str],
    tests: list[str],
    status: str = "planned",
    depends: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_, "title": title or f"Блок {id_}", "goal": goal, "depends_on": depends or [],
        "spec": "", "acceptance_tests": tests, "status": status, "passes": False, "incidents": 0,
    }  # fmt: skip


def junit(*cases: tuple[str, str, str]) -> str:
    rows = ""
    for classname, name, state in cases:
        inner = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>"}[state]
        rows += f'<testcase classname="{classname}" name="{name}">{inner}</testcase>'
    return f'<testsuites><testsuite name="pytest">{rows}</testsuite></testsuites>'


def make(
    root: Path,
    features: list[dict[str, Any]] | None,
    report: str | None = None,
    status: str = APPROVED,
    goal: str | None = None,
) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir(exist_ok=True)
    (root / "docs" / "GOAL.md").write_text(
        goal if goal is not None else GOAL_TEXT.format(status=status), encoding="utf-8"
    )
    if features is not None:
        data = json.dumps({"version": 1, "features": features}, ensure_ascii=False)
        (root / "state" / "features.json").write_text(data, encoding="utf-8")
    if report is not None:
        (root / "report.xml").write_text(report, encoding="utf-8")
    return root


def board(root: Path, *extra: str, report: bool = True) -> str:
    args = [sys.executable, str(SCRIPT), "--project", str(root), "--commit", "abc1234"]
    args += ["--date", "2026-10-03"]
    if report and (root / "report.xml").exists():
        args += ["--report", str(root / "report.xml")]
    done = subprocess.run(
        [*args, *extra], capture_output=True, text=True, encoding="utf-8", check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


PASS_A = ("tests.test_a", "test_one", "passed")
PASS_B = ("tests.test_b", "test_two", "passed")


def section(text: str, heading: str) -> str:
    head = f"## {heading}\n"
    assert head in text, heading
    return text.split(head, 1)[1].split("\n## ", 1)[0]


# ---------- готовность по тестам приёмки ----------


def test_a_block_is_done_only_when_its_acceptance_tests_ran_and_passed(tmp_path: Path) -> None:
    features = [feature("F1", ["G1"], ["tests/test_a.py"], "in_progress")]
    text = board(make(tmp_path, features, junit(PASS_A)))
    assert "| F1 | Блок F1 | ✅ готово |" in text
    assert "# Прогресс: 1 из 1 блоков готово · критерий G1 выполнен" in text


@pytest.mark.parametrize("state", ["failed", "skipped"])
def test_a_failed_or_skipped_acceptance_test_keeps_the_block_unfinished(
    tmp_path: Path, state: str
) -> None:
    features = [feature("F1", ["G1"], ["tests/test_a.py"], "in_progress")]
    text = board(make(tmp_path, features, junit(PASS_A, ("tests.test_a", "test_two", state))))
    assert "✅" not in text
    assert "| F1 | Блок F1 | 🔨 в работе |" in text


def test_a_block_whose_acceptance_tests_are_missing_from_the_report_is_not_done(
    tmp_path: Path,
) -> None:
    features = [feature("F1", ["G1"], ["tests/test_a.py", "tests/test_ghost.py"], "in_progress")]
    assert "✅" not in board(make(tmp_path, features, junit(PASS_A)))


def test_a_block_without_acceptance_tests_never_becomes_done(tmp_path: Path) -> None:
    features = [feature("F1", ["G1"], [], "in_progress")]
    assert "✅" not in board(make(tmp_path, features, junit(PASS_A)))


def test_a_block_is_not_done_while_a_block_it_depends_on_is_not(tmp_path: Path) -> None:
    features = [
        feature("F1", ["G1"], ["tests/test_a.py"], "in_progress"),
        feature("F2", ["G1"], ["tests/test_b.py"], "in_progress", ["F1"]),
    ]
    report = junit(("tests.test_a", "t", "failed"), PASS_B)
    text = board(make(tmp_path, features, report))
    assert "✅" not in text


def test_done_written_by_hand_without_passing_tests_is_flagged_and_not_shown_as_done(
    tmp_path: Path,
) -> None:
    features = [feature("F1", ["G1"], ["tests/test_a.py"], "done")]
    text = board(make(tmp_path, features, junit(("tests.test_a", "t", "failed"))))
    assert "✅" not in text
    assert "F1: помечен «готово», но тесты приёмки не прошли или не найдены в отчёте" in text


def test_a_done_block_that_depends_on_an_unfinished_one_is_flagged(tmp_path: Path) -> None:
    features = [
        feature("F1", ["G1"], [], "planned"),
        feature("F2", ["G1"], ["tests/test_b.py"], "done", ["F1"]),
    ]
    text = board(make(tmp_path, features, junit(PASS_B)))
    assert "F2: «готово», но зависит от неготового блока" in text
    assert "✅" not in text


def test_acceptance_matching_works_for_python_typescript_and_csharp_names() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    from parch_status import matches

    assert matches("tests/test_a.py", "tests.test_a::test_one")
    assert matches("tests/test_a.py", "tests.test_a.TestGroup::test_x[p]")
    assert not matches("tests/test_a.py", "tests.test_ab::test_one")
    assert matches("tests/orders.test.ts", "orders.test.ts::places an order")
    assert not matches("tests/orders.test.ts", "pricing.test.ts::x")
    assert matches("tests/Shop.Tests/OrderTests.cs", "Shop.Tests.OrderTests.Places")
    assert matches("tests/Shop.Tests/OrderTests.cs", "Shop.Tests.OrderTests.Rounds(x: 1.5)")
    assert not matches("tests/Shop.Tests/OrderTests.cs", "Shop.Tests.PricingTests.Places")


# ---------- итог, критерии, решения владельца ----------


def test_the_headline_counts_blocks_per_criterion(tmp_path: Path) -> None:
    features = [
        feature("F1", ["G1"], ["tests/test_a.py"], "in_progress"),
        feature("F2", ["G2"], ["tests/test_b.py"], "in_progress"),
        feature("F3", ["G2"], [], "planned"),
    ]
    text = board(make(tmp_path, features, junit(PASS_A, ("tests.test_b", "t", "failed"))))
    first = text.splitlines()[0]
    assert (
        first
        == "# Прогресс: 1 из 3 блоков готово · критерий G1 выполнен · G2 — 0 из 2 · G3 без блоков"
    )


def test_a_criterion_with_only_planned_blocks_is_not_started(tmp_path: Path) -> None:
    text = board(make(tmp_path, [feature("F1", ["G1"], [], "planned")]))
    assert "G1 не начат" in text.splitlines()[0]


def test_a_draft_goal_waits_for_the_owner_and_an_approved_one_does_not(tmp_path: Path) -> None:
    draft = board(make(tmp_path / "d", [], status="черновик, ждёт утверждения владельца"))
    assert "Цель `docs/GOAL.md` ещё не утверждена" in section(draft, "Нужно ваше решение")
    template_hint = "черновик. Замените на «Статус: утверждена владельцем, ДАТА»"
    hint = board(make(tmp_path / "h", [], status=template_hint))
    assert "ещё не утверждена" in hint  # подсказка с «ДАТА» не считается утверждением
    done = board(make(tmp_path / "a", [], status=APPROVED))
    assert "ещё не утверждена" not in done
    assert "ничего: всё идёт без вашего участия" in section(done, "Нужно ваше решение")


def test_blocked_blocks_and_their_incidents_are_put_in_front_of_the_owner(tmp_path: Path) -> None:
    root = make(
        tmp_path,
        [
            {**feature("F9", ["G1"], [], "blocked", title="Выгрузка в PDF"), "incident_budget": 3},
            feature("F8", ["G1"], [], "waiting_owner", title="Пилот на живом проекте"),
        ],
    )
    incidents = root / "state" / "incidents"
    incidents.mkdir()
    (incidents / "2026-10-14-F9-loop.md").write_text("# отчёт\n", encoding="utf-8")
    (incidents / "2026-10-15-F9-blocker.md").write_text("# отчёт\n", encoding="utf-8")
    (incidents / "2026-10-15-F2-loop.md").write_text("# другой блок\n", encoding="utf-8")
    text = board(root)
    decisions = section(text, "Нужно ваше решение")
    assert "**F8 «Пилот на живом проекте»** ждёт владельца" in decisions
    assert "F9" not in decisions  # заблокированный блок ждёт не владельца, а устранения причины
    assert "F9 «Выгрузка в PDF» — заблокирован (инцидентов: 2)" in section(text, "В работе")
    assert "| ⛔ заблокирован | — | 2 |" in text and "| 🙋 ждёт владельца |" in text
    assert "Замечания к плану" not in text  # у блока есть отчёты об инцидентах


def test_blocked_or_stuck_without_an_incident_report_is_flagged_on_the_board(
    tmp_path: Path,
) -> None:
    root = make(
        tmp_path,
        [feature("F1", ["G1"], [], "blocked"), feature("F2", ["G1"], [], "stuck")],
    )
    problems = section(board(root), "Замечания к плану")
    assert "F1: статус «заблокирован» без отчёта в state/incidents/" in problems
    assert "F2: статус «застрял» без отчёта в state/incidents/" in problems


def test_report_from_another_commit_is_called_out_with_the_report_commit(tmp_path: Path) -> None:
    root = make(tmp_path, [feature("F1", ["G1"], ["tests/test_a.py"])], junit(PASS_A))
    stale = board(root, "--report-commit", "9f8e7d6", "--report-same-tree", "no")
    assert (
        "> ⚠ **Отчёт тестов снят не с текущего коммита:** отчёт с `9f8e7d6`, табло на `abc1234`"
        in (stale)
    )
    assert "Это не текущий коммит `abc1234`" in section(stale, "Тесты")
    same = board(root, "--report-commit", "9f8e7d6", "--report-same-tree", "yes")
    assert "⚠" not in same and "коммите `9f8e7d6`" in section(same, "Тесты")
    unknown = board(root)  # коммит отчёта не передан: так тоже нельзя молчать
    assert "коммит отчёта не указан" in unknown
    assert "⚠" not in board(make(tmp_path / "n", []))  # без отчёта предупреждать не о чем


def test_adr_waiting_for_approval_and_open_questions_are_listed(tmp_path: Path) -> None:
    root = make(tmp_path, [])
    adr = root / "docs" / "adr"
    adr.mkdir()
    (adr / "0001-old.md").write_text("# ADR-0001. Старое\n\n- Статус: accepted\n", encoding="utf-8")
    (adr / "0002-new.md").write_text(
        "# ADR-0002. Новое решение\n\n- Статус: proposed\n", encoding="utf-8"
    )
    (adr / "0000-template.md").write_text("# ADR-0000\n\n- Статус: proposed\n", encoding="utf-8")
    (root / "docs" / "QUESTIONS.md").write_text(
        "| № | Вопрос | Почему | Варианты | Ответ владельца |\n|---|---|---|---|---|\n"
        "| 1 | Какой формат выгрузки? | нужен | PDF, Word | |\n"
        "| 2 | Нужна ли оплата? | нужен | да, нет | нет |\n",
        encoding="utf-8",
    )
    decisions = section(board(root), "Нужно ваше решение")
    assert "ADR-0002. Новое решение — ждёт утверждения владельца" in decisions
    assert "ADR-0001" not in decisions and "ADR-0000" not in decisions
    assert "Вопрос 1: Какой формат выгрузки?" in decisions
    assert "Вопрос 2" not in decisions


def test_open_pull_requests_are_shown_with_ci_state_and_owner_labels(tmp_path: Path) -> None:
    root = make(tmp_path, [])
    prs = [
        {"number": 41, "title": "Таблица", "isDraft": False, "labels": [],
         "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}]},
        {"number": 42, "title": "Защита", "isDraft": True,
         "labels": [{"name": "нужно решение владельца"}],
         "statusCheckRollup": [{"status": "IN_PROGRESS", "conclusion": ""}]},
        {"number": 43, "title": "Сломано", "isDraft": False, "labels": [],
         "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "FAILURE"}]},
    ]  # fmt: skip
    (root / "prs.json").write_text(json.dumps(prs, ensure_ascii=False), encoding="utf-8")
    text = board(root, "--prs", str(root / "prs.json"))
    working = section(text, "В работе")
    assert "**PR #41** «Таблица» — CI зелёный" in working
    assert "**PR #42** «Защита» — CI идёт, черновик" in working
    assert "**PR #43** «Сломано» — CI красный" in working
    decisions = section(text, "Нужно ваше решение")
    assert "PR #42" in decisions and "PR #41" not in decisions


# ---------- замечания к плану ----------


@pytest.mark.parametrize(
    ("features", "problem"),
    [
        ([feature("F1", [], [])], "F1: нет цели или она ссылается на критерий"),
        ([feature("F1", ["G9"], [])], "F1: нет цели или она ссылается на критерий"),
        ([feature("F1", ["G1"], [], depends=["F7"])], "F1: зависит от несуществующего блока F7"),
        ([feature("F1", ["G1"], [], status="weird")], "F1: неизвестный статус «weird»"),
        (
            [
                feature("F1", ["G1"], [], depends=["F2"]),
                feature("F2", ["G1"], [], depends=["F1"]),
            ],
            "цикл зависимостей между блоками",
        ),
    ],
)
def test_plan_problems_are_shown_instead_of_being_hidden(
    tmp_path: Path, features: list[dict[str, Any]], problem: str
) -> None:
    text = board(make(tmp_path, features))
    assert problem in section(text, "Замечания к плану")


def test_a_correct_plan_has_no_problem_section(tmp_path: Path) -> None:
    text = board(make(tmp_path, [feature("F1", ["G1"], [])]))
    assert "## Замечания к плану" not in text


# ---------- диаграммы ----------


def test_there_is_one_small_diagram_per_criterion_with_outside_blocks_dashed(
    tmp_path: Path,
) -> None:
    features = [
        feature("F1", ["G1"], ["tests/test_a.py"], "in_progress"),
        feature("F2", ["G2"], [], "planned", ["F1"]),
        feature(
            "F3",
            ["G2"],
            [],
            "blocked",
            ["F2"],
            title='Выгрузка "в" PDF с очень длинным названием блока',
        ),
    ]
    text = board(make(tmp_path, features, junit(PASS_A)))
    assert text.count("```mermaid") == 2  # G3 без блоков: диаграммы нет
    g2 = text.split("### G2", 1)[1].split("```mermaid", 1)[1].split("```", 1)[0]
    assert "F1 --> F2" in g2 and "F2 --> F3" in g2
    assert "class F1 outside" in g2 and "classDef outside" in g2
    assert "class F1 outside" in g2
    assert '"' not in g2.split("F3[", 1)[1].split("]", 1)[0].strip('"')  # кавычки в названии убраны
    assert "…" in g2  # длинное название сокращено
    assert "class F3 blocked" in g2


def test_without_blocks_the_plan_says_so(tmp_path: Path) -> None:
    text = board(make(tmp_path, []))
    assert "# Прогресс: блоков пока нет" in text
    assert "Блоков пока нет. Планировщик заводит их" in text
    assert "```mermaid" not in text


def test_without_features_json_the_board_still_builds(tmp_path: Path) -> None:
    text = board(make(tmp_path, None))
    assert "блоков пока нет" in text


# ---------- тесты, расход CI, динамика ----------


def test_tests_section_counts_failed_and_skipped(tmp_path: Path) -> None:
    report = junit(
        PASS_A, PASS_B, ("tests.test_c", "t", "failed"), ("tests.test_d", "t", "skipped")
    )
    text = board(make(tmp_path, [], report))
    tests = section(text, "Тесты")
    assert "Всего **4**" in tests
    assert "Упавших: **1**. Пропущенных: **1**." in tests
    assert "упал: tests.test_c::t" in tests


def test_the_previous_total_shows_the_trend(tmp_path: Path) -> None:
    root = make(tmp_path, [], junit(PASS_A, PASS_B))
    (root / "previous.md").write_text("## Тесты\n- Всего **935**.\n", encoding="utf-8")
    assert "Всего **2** (было 935)" in board(root, "--previous", str(root / "previous.md"))


def test_without_a_report_the_board_says_so(tmp_path: Path) -> None:
    assert "Отчёта о запуске тестов пока нет." in board(make(tmp_path, []))


def test_ci_cost_is_only_a_link_to_github(tmp_path: Path) -> None:
    ci = section(board(make(tmp_path, [])), "Расход CI")
    assert "https://github.com/settings/billing/summary" in ci
    assert "https://github.com/settings/billing/budgets" in ci
    assert "минут" not in ci.replace("Минуты и деньги", "")


def test_the_report_may_come_from_any_supported_format(tmp_path: Path) -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    import parch_ci

    trx = tmp_path / "r.trx"
    trx.write_text(
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
        '<UnitTestResult testName="A.B.C.D" outcome="Passed"/></Results></TestRun>',
        encoding="utf-8",
    )
    js = tmp_path / "r.json"
    js.write_text(
        '{"testResults":[{"name":"/p/a.test.ts","assertionResults":'
        '[{"status":"passed","fullName":"x"}]}]}',
        encoding="utf-8",
    )
    xml = tmp_path / "r.xml"
    xml.write_text(junit(("tests.t", "n@csharp", "passed")), encoding="utf-8")
    assert parch_ci.any_report_outcomes(trx) == {"A.B.C.D": "passed"}
    assert parch_ci.any_report_outcomes(js) == {"a.test.ts::x": "passed"}
    assert parch_ci.any_report_outcomes(xml) == {"tests.t::n": "passed"}  # суффикс xdist отброшен
    bad = tmp_path / "bad.xml"
    bad.write_text("<html/>", encoding="utf-8")
    with pytest.raises(parch_ci.ToolError):
        parch_ci.any_report_outcomes(bad)


# ---------- командная строка ----------


def test_the_board_can_be_written_to_a_file_in_utf8(tmp_path: Path) -> None:
    root = make(tmp_path, [feature("F1", ["G1"], [])])
    out = tmp_path / "STATUS.md"
    board(root, "--out", str(out))
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Прогресс:") and "\r\n" not in text


def test_a_broken_features_file_is_a_failure_not_an_empty_board(tmp_path: Path) -> None:
    root = make(tmp_path, [])
    (root / "state" / "features.json").write_text("{не json", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 1
    assert "[parch:status] ПРОВАЛ" in done.stderr


# ---------- табло самого продукта ----------


def test_the_product_has_a_consistent_goal_and_plan() -> None:
    sys.path.insert(0, str(SCRIPT.parent))
    from parch_status import build

    text = build(REPO, None, "x", "2026-10-03", None, [])
    assert "## Замечания к плану" not in text, text.split("## Замечания к плану")[-1][:400]
    features = json.loads((REPO / "state" / "features.json").read_text(encoding="utf-8"))[
        "features"
    ]
    ids = [f["id"] for f in features]
    assert len(ids) == len(set(ids)) >= 17
    for item in features:
        for path in item["acceptance_tests"]:
            assert (REPO / path).is_file(), f"{item['id']}: нет файла тестов приёмки {path}"


def test_the_product_goal_lists_criteria_g1_to_g6() -> None:
    goal = (REPO / "docs" / "GOAL.md").read_text(encoding="utf-8")
    assert "# GOAL — цель продукта «ProjectArchitect»" in goal
    for criterion in ("G1", "G2", "G3", "G4", "G5", "G6"):
        assert f"**{criterion}.**" in goal


# ---------- workflow пересчёта и ADR ----------


def run_standard(root: Path) -> subprocess.CompletedProcess[str]:
    script = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
    return subprocess.run(
        [sys.executable, str(script), "standard", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def test_the_state_workflow_is_the_only_one_allowed_to_run_on_push_and_passes_the_standard(
    tmp_path: Path,
) -> None:
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    text = TEMPLATE_STATE.read_text(encoding="utf-8")
    (folder / "state.yml").write_text(text, encoding="utf-8")
    done = run_standard(tmp_path)
    assert done.returncode == 0, done.stdout
    (folder / "other.yml").write_text(
        text, encoding="utf-8"
    )  # то же под другим именем: push нельзя
    assert "CI запускается только на pull_request" in run_standard(tmp_path).stdout


def test_the_state_workflow_runs_no_tests_and_publishes_only_to_the_status_branch() -> None:
    text = TEMPLATE_STATE.read_text(encoding="utf-8")
    on_block = text.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert on_block.strip() == "push:\n    branches: [main]"  # только push в main
    assert "timeout-minutes: 10" in text and "runs-on: ubuntu-latest" in text
    assert "git push origin HEAD:status" in text
    assert "git push origin HEAD:main" not in text and "pytest" not in text
    assert "gh run download" in text and "-n test-report" in text
    assert "--report-commit" in text and "--report-same-tree" in text


def test_the_python_template_hands_its_report_to_the_state_job() -> None:
    template = (REPO / "plugin" / "templates" / "ci" / "python.yml").read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in template and "name: test-report" in template


def test_adr_0012_records_the_choice_of_the_status_branch() -> None:
    adr = (REPO / "docs" / "adr" / "0012-tablo-status-na-vetke.md").read_text(encoding="utf-8")
    assert "- Статус: accepted (утверждён владельцем, 2026-10-03)" in adr
    for fact in (
        "ветке `status`",
        "обход",
        "личный токен",
        "ссылка на страницы GitHub",
        "правила 10",
    ):
        assert fact in adr, fact


def test_the_product_goal_is_approved_and_g6_is_tied_to_blocks() -> None:
    goal = (REPO / "docs" / "GOAL.md").read_text(encoding="utf-8")
    assert "Статус: утверждена владельцем, 2026-10-03" in goal
    assert "**G6.** ИИ не теряет нить проекта" in goal
    blocks = json.loads((REPO / "state" / "features.json").read_text(encoding="utf-8"))["features"]
    g6 = {b["id"]: b["title"] for b in blocks if "G6" in b["goal"]}
    assert {"F13", "F14", "F18"} <= set(
        g6
    )  # инциденты и петли, проверка стандарта, прослеживаемость
    assert "Прослеживаемость цели до кода" in g6.values()
    pilot = next(b for b in blocks if b["id"] == "F17")
    assert pilot["status"] == "waiting_owner"  # пилот ждёт владельца, а не «заблокирован»


# ---------- расход CI: минуты за неделю и на один PR ----------


def ci_run(
    pr: int | None, day: str, conclusion: str, *jobs: tuple[str, str, str]
) -> dict[str, Any]:
    return {
        "id": 1, "pr": pr, "created": f"{day}T10:00:00Z", "conclusion": conclusion,
        "jobs": [
            {"labels": [label], "started_at": f"{day}T10:00:00Z", "completed_at": f"{day}T{end}Z"}
            for label, end, _ in jobs
        ],
    }  # fmt: skip


def ci_board(tmp_path: Path, runs: list[dict[str, Any]]) -> str:
    root = make(tmp_path, [])
    (root / "ci-runs.json").write_text(json.dumps(runs), encoding="utf-8")
    return section(board(root, "--ci-runs", str(root / "ci-runs.json")), "Расход CI")


def test_ci_minutes_are_counted_per_job_rounded_up_with_the_windows_multiplier(
    tmp_path: Path,
) -> None:
    runs = [
        ci_run(5, "2026-10-02", "success", ("ubuntu-latest", "10:10:30", "")),  # 10,5 -> 11
        ci_run(5, "2026-10-02", "failure", ("windows-latest", "10:01:00", "")),  # 1 x2 -> 2
        ci_run(6, "2026-10-03", "success", ("ubuntu-latest", "10:05:00", "")),  # 5
        ci_run(None, "2026-10-03", "success", ("ubuntu-latest", "10:03:00", "")),  # 3, вне PR
        ci_run(4, "2026-09-20", "success", ("ubuntu-latest", "10:59:00", "")),  # старше недели
    ]
    out = ci_board(tmp_path, runs)
    assert "За 7 дней: **21** минут квоты в 4 прогонах" in out
    assert "Впустую, то есть упало или отменено: **2** минут в 1 прогонах" in out
    assert "в среднем **9** минут (PR за неделю: 2); больше всего у PR #5: 13 минут" in out
    assert "| #5 | 2 | 13 | 1 |" in out and "| #6 | 1 | 5 | 0 |" in out
    assert "#4" not in out  # прогон старше недели не считается
    assert "Вне PR (после слияния, табло): 3 минут" in out
    assert "https://github.com/settings/billing/summary" in out  # ссылка на GitHub остаётся


def test_ci_section_without_runs_data_is_only_the_link_and_with_no_prs_says_so(
    tmp_path: Path,
) -> None:
    quiet = ci_board(
        tmp_path, [ci_run(None, "2026-10-03", "success", ("ubuntu-latest", "10:02:00", ""))]
    )
    assert "За 7 дней: **2** минут квоты в 1 прогонах" in quiet
    assert "за неделю PR с прогонами не было" in quiet
    assert "Нет данных" not in quiet


def test_the_state_workflow_reads_the_report_of_the_full_run_and_counts_ci_minutes() -> None:
    text = TEMPLATE_STATE.read_text(encoding="utf-8")
    assert "for wf in full.yml ci.yml" in text  # отчёт даёт полный прогон
    assert "--ci-runs" in text and "actions/runs" in text and "/jobs?per_page=100" in text
    assert "continue-on-error: true" in text  # сбой подсчёта минут не ломает табло


def test_the_state_workflow_finds_the_pr_of_a_run_by_branch_when_the_run_data_has_none() -> None:
    text = TEMPLATE_STATE.read_text(encoding="utf-8")
    assert "gh pr list --state all" in text and "--argjson map" in text
    assert ".pull_requests[0].number // $map[.head_branch]" in text  # слитый PR: поле пустое
