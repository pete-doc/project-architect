# ruff: noqa: E501
"""F14, PR 1: правила состава проекта в проверке `standard`: обязательные файлы, конкурирующие инструкции, `.md` вне мест.

Плохие примеры, которые проверка обязана остановить на новом проекте: нет обязательного файла, второй файл инструкций,
CLAUDE.md, не ссылающийся на AGENTS.md, `.md` вне разрешённых мест. Существующий проект сначала получает только
предупреждение; после записи долга владельцем включается храповик (старое допускается, новое падает).
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

WORKFLOW = """name: ci

on:
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - run: echo hi
"""


CONSTITUTION = (
    "# CONSTITUTION\n\n## Целевая ОС\n\nWindows 11\n\n## Бюджеты\n\nБюджет инцидентов на блок: 2\n"
)


def write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def standard(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def managed(root: Path, existing: bool = False) -> Path:
    """Подключённый проект со всеми обязательными файлами."""
    write(root / ".github" / "parch" / "parch_ci.py", "# установленный скрипт проверок\n")
    write(root / ".github" / "workflows" / "ci.yml", WORKFLOW)
    for rel in ("docs/GOAL.md", "docs/MODULES.md", "docs/INCIDENT_TEMPLATE.md"):
        write(root / rel)
    write(root / "docs" / "CONSTITUTION.md", CONSTITUTION)
    write(root / "state" / "features.json", json.dumps({"version": 1, "features": []}))
    baseline: dict[str, object] = {"version": 1}
    if existing:
        baseline["existing_project"] = True
    write(root / "state" / "baseline.json", json.dumps(baseline))
    return root


def test_a_complete_new_project_passes(tmp_path: Path) -> None:
    done = standard(managed(tmp_path))
    assert done.returncode == 0, done.stdout


def test_a_new_project_without_a_required_file_fails_and_names_the_file(tmp_path: Path) -> None:
    root = managed(tmp_path)
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert (
        done.returncode == 1 and "docs/MODULES.md" in done.stdout and "карта модулей" in done.stdout
    )


def test_goal_and_constitution_may_live_in_the_project_root(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "GOAL.md")
    write(root / "CONSTITUTION.md", CONSTITUTION)
    (root / "docs" / "GOAL.md").unlink()
    (root / "docs" / "CONSTITUTION.md").unlink()
    assert standard(root).returncode == 0


def test_a_second_instruction_file_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / ".cursorrules")
    write(root / ".cursor" / "rules" / "style.mdc")
    done = standard(root)
    assert (
        done.returncode == 1
        and ".cursorrules" in done.stdout
        and ".cursor/rules/style.mdc" in done.stdout
    )


def test_claude_md_must_point_to_agents_md_when_both_exist(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "AGENTS.md", "# правила\n")
    write(
        root / "CLAUDE.md",
        "# свои правила Claude, расходящиеся с AGENTS\n".replace("AGENTS", "основными"),
    )
    done = standard(root)
    assert done.returncode == 1 and "CLAUDE.md не ссылается на AGENTS.md" in done.stdout
    write(root / "CLAUDE.md", "@AGENTS.md\n\n## Специфика Claude Code\n")
    assert standard(root).returncode == 0
    (root / "AGENTS.md").unlink()
    write(root / "CLAUDE.md", "# единственная инструкция\n")
    assert standard(root).returncode == 0


def test_markdown_outside_the_allowed_places_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "notes" / "plan.md")
    write(root / "src" / "NOTES.md")
    done = standard(root)
    assert done.returncode == 1 and "notes/plan.md" in done.stdout and "src/NOTES.md" in done.stdout


def test_allowed_markdown_places_do_not_fail(tmp_path: Path) -> None:
    root = managed(tmp_path)
    for rel in (
        "README.md",
        "docs/specs/F1.md",
        "state/notes.md",
        ".github/pull_request_template.md",
        ".claude/skills/a/SKILL.md",
        "plugin/agents/role.md",
        "parch-analysis/ANALYSIS_REPORT.md",
        "node_modules/pkg/README.md",
    ):
        write(root / rel)
    assert standard(root).returncode == 0


def test_a_project_without_the_installed_ci_script_is_not_subject_to_the_composition_rules(
    tmp_path: Path,
) -> None:
    write(tmp_path / ".github" / "workflows" / "ci.yml", WORKFLOW)
    write(tmp_path / "notes" / "plan.md")
    done = standard(tmp_path)
    assert done.returncode == 0 and "не применяются" in done.stdout


def test_an_existing_project_is_only_warned_until_the_owner_records_the_debt(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert (
        done.returncode == 0 and "Предупреждение" in done.stdout and "notes/plan.md" in done.stdout
    )


def test_the_owner_records_the_debt_then_new_violations_fail_and_fixed_ones_shrink_it(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    assert standard(root, "--update").returncode == 0  # без флага владельца долг не записывается
    assert not (root / "state" / "standard-baseline.json").exists()
    assert standard(root, "--update", "--accept-new").returncode == 0
    debt = json.loads((root / "state" / "standard-baseline.json").read_text(encoding="utf-8"))
    assert debt["violations"] == ["md:notes/plan.md"]
    assert standard(root).returncode == 0
    write(root / "notes" / "extra.md")
    done = standard(root)
    assert (
        done.returncode == 1
        and "notes/extra.md" in done.stdout
        and "notes/plan.md" not in done.stdout
    )
    (root / "notes" / "extra.md").unlink()
    (root / "notes" / "plan.md").unlink()
    assert standard(root, "--update").returncode == 0
    # долг погашен, но файл остался: храповик не сбрасывается, новое нарушение снова падает
    debt = json.loads((root / "state" / "standard-baseline.json").read_text(encoding="utf-8"))
    assert debt["violations"] == []
    write(root / "notes" / "again.md")
    assert standard(root).returncode == 1


def test_claude_md_that_only_mentions_agents_md_still_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "AGENTS.md", "# правила\n")
    write(root / "CLAUDE.md", "Не читай AGENTS.md, у меня свои правила.\n")
    done = standard(root)
    assert done.returncode == 1 and "CLAUDE.md не ссылается на AGENTS.md" in done.stdout


def test_markdown_is_not_hidden_in_nested_docs_agents_or_build_folders(tmp_path: Path) -> None:
    root = managed(tmp_path)
    hidden = (
        "src/docs/a.md",
        "src/agents/deep/b.md",
        "src/agents/x.md",
        "src/SKILL.md",
        "build/c.md",
        "env/d.md",
        "bin/e.md",
    )
    for rel in hidden:
        write(root / rel)
    done = standard(root)
    for rel in hidden:
        assert rel in done.stdout, rel


def test_a_missing_parch_standard_module_fails_loudly_instead_of_crashing(tmp_path: Path) -> None:
    import shutil

    ci = tmp_path / "ci"
    ci.mkdir()
    shutil.copy(SCRIPT, ci / "parch_ci.py")
    for name in ("parch_catalog.py", "parch_libraries.py"):
        shutil.copy(SCRIPT.parent / name, ci / name)
    root = managed(tmp_path / "proj")
    done = subprocess.run(
        [sys.executable, str(ci / "parch_ci.py"), "standard", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 1 and "parch_standard.py" in done.stdout
    assert "Traceback" not in done.stderr


def test_with_a_debt_file_a_removed_required_file_fails_even_on_an_existing_project(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    standard(root, "--update", "--accept-new")
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert done.returncode == 1 and "docs/MODULES.md" in done.stdout


def test_a_project_just_created_by_init_project_passes_the_composition_rules(
    tmp_path: Path,
) -> None:
    from test_skills import init

    init(tmp_path)
    done = standard(tmp_path)
    assert done.returncode == 0, done.stdout
    assert "не применяются" not in done.stdout  # правила действительно проверялись


def test_the_target_ci_templates_run_the_standard_check() -> None:
    for name in ("python.yml", "typescript.yml", "csharp.yml", "powershell.yml"):
        text = (REPO / "plugin" / "templates" / "ci" / "circleci" / name).read_text(
            encoding="utf-8"
        )
        assert "parch_ci.py standard" in text, name


def test_the_product_repository_is_subject_to_the_composition_rules_with_a_recorded_debt() -> None:
    """Продукт подключён по CONSTITUTION (F25, шаг 2): правила состава на нём работают.

    Нарушения записаны долгом (`standard-baseline.json`), поэтому код 0 и строка
    «Нарушений состава в долге: N»; признаков «не подключён» в выводе нет.
    """
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(REPO)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"Нарушений состава в долге: \d+", done.stdout), done.stdout
    for stale in ("не применяются", "не подключ", "не проверяются"):
        assert stale not in done.stdout, done.stdout


# ---------- F14, PR 3: карточка соответствия P1–P13 и версия стандарта ----------

STATUS_SCRIPT = SCRIPT.parent / "parch_status.py"
ANALYZE = REPO / "plugin" / "skills" / "analyze-existing" / "scripts" / "analyze.py"
CARD_ROW = re.compile(r"^\| (P\d+) \| [^|]*\| \S+ ([^|]+?) \| ([^|]+?) \|", re.MULTILINE)
APPROVED_GOAL = (
    "# GOAL — цель продукта «Тест»\n\n> **Статус: утверждена владельцем, 2026-10-01.**\n\n"
    "- **G1.** Заказ считается верно\n"
)


def status_board(project: Path, previous: Path | None = None) -> str:
    args = [sys.executable, str(STATUS_SCRIPT), "--project", str(project), "--date", "2026-10-10"]
    if previous is not None:
        args += ["--previous", str(previous)]
    done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    return done.stdout


def grades(board: str) -> dict[str, str]:
    return {point: grade for point, grade, _ in CARD_ROW.findall(board)}


def trends(board: str) -> dict[str, str]:
    return {point: trend for point, _, trend in CARD_ROW.findall(board)}


def with_version(root: Path, version: str) -> Path:
    write(
        root / "docs" / "CONSTITUTION.md",
        CONSTITUTION + f"\n## Версия стандарта\n\nProjectArchitect {version} (docs/STANDARD.md).\n",
    )
    return root


def standard_section(heading: str) -> str:
    text = (REPO / "docs" / "STANDARD.md").read_text(encoding="utf-8")
    return text.split(f"\n## {heading}", 1)[1].split("\n## ", 1)[0]


def test_the_card_has_exactly_the_points_of_section_9_of_the_standard(tmp_path: Path) -> None:
    import parch_standard

    points = re.findall(r"^\| (P\d+) \|", standard_section("9."), re.MULTILINE)
    assert [point for point, _ in parch_standard.CARD] == points
    assert list(grades(status_board(managed(tmp_path)))) == points


def test_section_11_and_the_f14_spec_name_the_same_number_of_points_as_section_9() -> None:
    """Плохой пример: «P1–P12» в разделе 11 при 13 пунктах раздела 9 (так было до F14, PR 3)."""
    points = re.findall(r"^\| (P\d+) \|", standard_section("9."), re.MULTILINE)
    expected = f"P1–{points[-1]}"
    assert expected in standard_section("11.")
    spec = (REPO / "docs" / "specs" / "F14-standard-full.md").read_text(encoding="utf-8")
    assert expected in spec
    for text in (standard_section("11."), spec):
        assert re.findall(r"P1–P\d+", text) == [expected] * len(re.findall(r"P1–P\d+", text))


def test_the_board_shows_the_card_instead_of_the_promise(tmp_path: Path) -> None:
    board = status_board(managed(tmp_path))
    assert "## Соответствие стандарту" in board
    assert "появится вместе с полной проверкой" not in board
    assert re.search(r"По стандарту \*\*\d+\*\* из 13 пунктов", board), board


def test_a_missing_or_unapproved_goal_lowers_p1(tmp_path: Path) -> None:
    root = managed(tmp_path)
    assert grades(status_board(root))["P1"] == "частично"  # файл есть, критериев и утверждения нет
    (root / "docs" / "GOAL.md").unlink()
    assert grades(status_board(root))["P1"] == "отсутствует"
    write(root / "docs" / "GOAL.md", APPROVED_GOAL)
    assert grades(status_board(root))["P1"] == "по стандарту"


def test_a_second_instruction_file_lowers_p3(tmp_path: Path) -> None:
    root = managed(tmp_path)
    assert grades(status_board(root))["P3"] == "отсутствует"  # инструкций для ИИ нет вовсе
    write(root / "AGENTS.md", "# правила\n")
    assert grades(status_board(root))["P3"] == "по стандарту"
    write(root / ".cursorrules")
    assert grades(status_board(root))["P3"] == "частично"


def test_markdown_outside_the_allowed_places_lowers_p5(tmp_path: Path) -> None:
    root = managed(tmp_path)
    assert grades(status_board(root))["P5"] == "по стандарту"
    write(root / "notes" / "plan.md")
    board = status_board(root)
    assert grades(board)["P5"] == "частично" and ".md вне разрешённых мест: 1" in board


def test_ci_cost_violations_lower_p13_and_no_ci_at_all_is_absent(tmp_path: Path) -> None:
    root = managed(tmp_path)
    assert grades(status_board(root))["P13"] == "по стандарту"
    write(
        root / ".github" / "workflows" / "ci.yml", WORKFLOW.replace("    timeout-minutes: 20\n", "")
    )
    assert grades(status_board(root))["P13"] == "частично"
    (root / ".github" / "workflows" / "ci.yml").unlink()
    assert grades(status_board(root))["P13"] == "отсутствует"


def test_the_card_reads_the_circleci_config_that_init_builds(tmp_path: Path) -> None:
    from test_status import assembled_config

    root = managed(tmp_path)
    (root / ".github" / "workflows" / "ci.yml").unlink()
    config = assembled_config(["python"])
    write(root / ".circleci" / "config.yml", config)
    card = grades(status_board(root))
    assert card["P13"] == "по стандарту" and card["P7"] != "отсутствует"
    assert "проверка standard в CI: да" in status_board(root)
    write(root / ".circleci" / "config.yml", config.replace("parch_ci.py standard", "true"))
    assert "проверка standard в CI: нет" in status_board(root)
    write(root / ".circleci" / "config.yml", config + "\n# x\n  nightly:\n    schedule:\n")
    assert grades(status_board(root))["P13"] == "частично"


def test_a_missing_target_os_lowers_p13(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "docs" / "CONSTITUTION.md", "# CONSTITUTION\n\nБюджет инцидентов на блок: 2\n")
    board = status_board(root)
    assert grades(board)["P13"] == "частично" and "целевая ОС в CONSTITUTION.md: нет" in board


def test_a_long_claude_md_without_agents_md_lowers_p3(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "CLAUDE.md", "# правила\n" + "строка\n" * 200)
    board = status_board(root)
    assert grades(board)["P3"] == "частично" and "строк в CLAUDE.md: 201" in board


def test_a_blocked_block_without_an_incident_report_lowers_p10(tmp_path: Path) -> None:
    root = managed(tmp_path)
    assert grades(status_board(root))["P10"] == "по стандарту"
    block = {"id": "F1", "title": "Блок", "goal": ["G1"], "depends_on": [], "status": "blocked"}
    write(root / "state" / "features.json", json.dumps({"version": 1, "features": [block]}))
    assert grades(status_board(root))["P10"] == "частично"


def test_what_the_repository_cannot_show_stays_a_question_to_the_owner(tmp_path: Path) -> None:
    card = grades(status_board(managed(tmp_path)))
    assert card["P8"] == card["P12"] == "вопрос владельцу"


def test_the_card_shows_the_trend_against_the_previous_board(tmp_path: Path) -> None:
    root = managed(tmp_path / "proj")
    first = status_board(root)
    assert set(trends(first).values()) == {"впервые"}
    previous = tmp_path / "previous.md"
    previous.write_text(first, encoding="utf-8")
    write(root / "docs" / "GOAL.md", APPROVED_GOAL)
    write(root / "notes" / "plan.md")
    second = status_board(root, previous)
    moves = trends(second)
    assert moves["P1"] == "↑ лучше, было «частично»"
    assert moves["P5"] == "↓ хуже, было «по стандарту»"
    assert moves["P13"] == "без изменений"


def test_the_summary_says_how_many_points_were_at_the_standard_before(tmp_path: Path) -> None:
    root = managed(tmp_path / "proj")
    previous = tmp_path / "previous.md"
    previous.write_text(status_board(root), encoding="utf-8")
    before = sum(grade == "по стандарту" for grade in grades(previous.read_text("utf-8")).values())
    write(root / "docs" / "GOAL.md", APPROVED_GOAL)
    assert f"из 13 пунктов (было {before})" in status_board(root, previous)


def test_without_parch_standard_the_board_says_so_instead_of_crashing(tmp_path: Path) -> None:
    import shutil

    scripts = tmp_path / "ci"
    scripts.mkdir()
    for name in ("parch_status.py", "parch_ci.py", "parch_catalog.py", "parch_libraries.py"):
        shutil.copy(SCRIPT.parent / name, scripts / name)
    root = managed(tmp_path / "proj")
    done = subprocess.run(
        [sys.executable, str(scripts / "parch_status.py"), "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "Карточка не построена" in done.stdout and "parch_standard.py" in done.stdout


def test_an_older_standard_version_shows_what_changed_and_only_warns(tmp_path: Path) -> None:
    root = with_version(managed(tmp_path), "1.2")
    done = standard(root)
    assert done.returncode == 0, done.stdout
    for text in ("записан на стандарт 1.2, действует 1.3", "1.3: стоимость CI", "P13"):
        assert text in done.stdout, text
    assert "/parch:analyze-existing" in done.stdout
    board = status_board(root)
    assert "Стандарт проекта: ProjectArchitect 1.2, действующая версия 1.3." in board
    assert "- 1.3: стоимость CI" in board


def test_the_current_version_is_quiet_and_a_missing_or_newer_one_is_named(tmp_path: Path) -> None:
    import parch_standard

    root = with_version(managed(tmp_path), parch_standard.CURRENT_VERSION)
    assert "записан на стандарт" not in standard(root).stdout
    write(root / "docs" / "CONSTITUTION.md", CONSTITUTION)
    assert "нет раздела «Версия стандарта»" in standard(root).stdout
    with_version(root, "9.9")
    assert "обновите плагин" in standard(root).stdout


def test_the_version_history_ends_with_the_current_version_and_matches_the_standard() -> None:
    import parch_ci
    import parch_standard

    assert parch_standard.CURRENT_VERSION == parch_ci.STANDARD_VERSION
    text = (REPO / "docs" / "STANDARD.md").read_text(encoding="utf-8")
    points = {point for point, _ in parch_standard.CARD}
    for version, what, touched in parch_standard.STANDARD_CHANGES:
        assert f"Что изменилось в {version}" in text, version
        assert what and set(touched) <= points, version


def test_analyze_existing_shows_the_changes_since_the_project_version(tmp_path: Path) -> None:
    def inventory(project: Path) -> dict[str, Any]:
        done = subprocess.run(
            [sys.executable, str(ANALYZE)],
            input=json.dumps({"command": "inventory", "project_dir": str(project)}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)["standard_version"]

    old = with_version(managed(tmp_path / "old"), "1.2")
    found = inventory(old)
    assert found["project"] == "1.2" and found["current"] == "1.3"
    assert [c["version"] for c in found["changes"]] == ["1.3"]
    assert "P13" in found["changes"][0]["points"] and found["notes"]
    unknown = managed(tmp_path / "unknown")  # подключён, строки версии нет: вся история
    found = inventory(unknown)
    assert found["project"] is None and found["notes"]
    assert [c["version"] for c in found["changes"]] == ["1.2", "1.3"]
    plain = tmp_path / "plain"
    write(plain / "src" / "app.py", "x = 1\n")
    assert inventory(plain) == {"project": None, "current": "1.3", "changes": [], "notes": []}


def test_the_product_board_carries_the_full_card() -> None:
    card = grades(status_board(REPO))
    assert len(card) == 13 and card["P1"] == "по стандарту", card
