# ruff: noqa: E501
"""`/parch:analyze-existing` (упрощённая версия): только чтение, карточка P1-P13, проверка «код не менялся»."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "plugin" / "skills" / "analyze-existing"
SCRIPT = SKILL / "scripts" / "analyze.py"
GOOD_WORKFLOW = (
    "name: ci\non:\n  pull_request:\nconcurrency:\n  group: x\n  cancel-in-progress: true\n"
    "jobs:\n  check:\n    runs-on: ubuntu-latest\n    timeout-minutes: 10\n    steps:\n      - run: echo\n"
)
BAD_WORKFLOW = "name: ci\non:\n  push:\njobs:\n  check:\n    runs-on: macos-latest\n    steps:\n      - run: echo\n"


def call(payload: dict[str, Any], expect: int = 0) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert done.returncode == expect, done.stdout + done.stderr
    return json.loads(done.stdout) if expect == 0 else {"error": done.stderr}


def inventory(project: Path) -> dict[str, Any]:
    return call({"command": "inventory", "project_dir": str(project)})


def card(project: Path) -> dict[str, str]:
    return {row["id"]: row["status"] for row in inventory(project)["p_card"]}


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def listing(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
         *args],
        cwd=root, check=True, capture_output=True, timeout=60,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path / "src" / "app.py", "x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


# ---------- факты и карточка ----------


def test_an_empty_project_is_absent_everywhere_and_asks_the_three_questions(tmp_path: Path) -> None:
    result = card(tmp_path)
    for point in ("P1", "P2", "P3", "P6", "P7", "P9", "P10", "P11", "P13"):
        assert result[point] == "отсутствует", point
    for point in ("P4", "P8", "P12"):
        assert result[point] == "вопрос владельцу", point  # по файлам не определить, не угадываем
    questions = inventory(tmp_path)["questions"]
    assert {q["id"] for q in questions} == {"P4", "P8", "P12"}


def test_inventory_counts_languages_tests_and_skips_dependencies(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x = 1\n")
    write(tmp_path / "web" / "b.ts", "export {}\n")
    write(tmp_path / "web" / "node_modules" / "dep" / "c.ts", "export {}\n")
    write(tmp_path / "svc" / "D.cs", "class D {}\n")
    write(tmp_path / "ops" / "e.ps1", "Write-Host 1\n")
    write(tmp_path / "tests" / "test_a.py", "def test_a() -> None: ...\n")
    facts = inventory(tmp_path)
    assert facts["languages"] == {"python": 2, "typescript": 1, "csharp": 1, "powershell": 1}
    assert facts["test_files"] >= 1


def test_goal_plan_and_modules_are_recognised(tmp_path: Path) -> None:
    write(tmp_path / "docs" / "GOAL.md", "# GOAL\n\n- **G1.** Заказ считается верно\n")
    assert card(tmp_path)["P1"] == "по стандарту"
    write(tmp_path / "docs" / "GOAL.md", "# GOAL\n\nХотим хороший магазин.\n")
    assert card(tmp_path)["P1"] == "частично"  # цель есть, критериев нет
    write(tmp_path / "state" / "features.json", '{"version": 1, "features": []}')
    assert card(tmp_path)["P2"] == "частично"
    write(tmp_path / "docs" / "MODULES.md", "| Модуль | Путь |\n")
    assert card(tmp_path)["P2"] == "по стандарту"


def test_competing_and_long_instruction_files_are_flagged(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "Коротко.\n")
    assert card(tmp_path)["P3"] == "по стандарту"  # один короткий файл
    write(tmp_path / ".cursorrules", "Другие правила.\n")
    assert card(tmp_path)["P3"] == "частично"  # два места
    write(tmp_path / ".cursorrules", "")
    (tmp_path / ".cursorrules").unlink()
    write(tmp_path / "CLAUDE.md", "строка\n" * 151)
    assert card(tmp_path)["P3"] == "частично"  # длиннее 150 строк


def test_notes_outside_docs_count_but_readme_and_docs_do_not(tmp_path: Path) -> None:
    write(tmp_path / "README.md", "x\n")
    write(tmp_path / "docs" / "a.md", "x\n")
    assert card(tmp_path)["P5"] == "по стандарту"
    for number in range(11):
        write(tmp_path / "notes" / f"plan{number}.md", "x\n")
    facts = inventory(tmp_path)
    assert facts["markdown_outside_docs_total"] == 11
    assert {r["id"]: r["status"] for r in facts["p_card"]}["P5"] == "отсутствует"


def test_workflows_without_timeout_with_push_or_macos_are_flagged(tmp_path: Path) -> None:
    write(tmp_path / ".github" / "workflows" / "ci.yml", GOOD_WORKFLOW)
    assert card(tmp_path)["P13"] == "по стандарту"
    write(tmp_path / ".github" / "workflows" / "old.yml", BAD_WORKFLOW)
    why = {r["id"]: r["why"] for r in inventory(tmp_path)["p_card"]}["P13"]
    assert card(tmp_path)["P13"] == "частично" and "old.yml" in why and "ci.yml" not in why


def test_adr_incidents_lessons_and_hooks_move_the_card(tmp_path: Path) -> None:
    for number in (1, 2, 3):
        write(tmp_path / "docs" / "adr" / f"000{number}-x.md", "# ADR\n")
    write(tmp_path / "state" / "incidents" / "2026-10-01-F1-loop.md", "# инцидент\n")
    write(tmp_path / "docs" / "LESSONS.md", "# уроки\n")
    write(tmp_path / ".claude" / "settings.json", '{"hooks": {}, "permissions": {}}')
    result = card(tmp_path)
    assert result["P6"] == "по стандарту" and result["P10"] == "частично"
    assert result["P11"] == "частично" and result["P7"] == "частично"  # CI и git нет


# ---------- ничего не меняет ----------


def test_inventory_creates_and_changes_nothing(tmp_path: Path) -> None:
    write(tmp_path / "src" / "app.py", "x = 1\n")
    write(tmp_path / "docs" / "GOAL.md", "# GOAL\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    names = listing(tmp_path)
    inventory(tmp_path)
    assert listing(tmp_path) == names
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_verify_accepts_only_the_report_and_catches_a_changed_source_file(repo: Path) -> None:
    facts = inventory(repo)
    assert facts["git"]["is_repo"] is True and facts["git"]["clean"] is True
    before = facts["git"]["snapshot"]
    write(repo / "analysis" / "ANALYSIS_REPORT.md", "# Отчёт\n")
    done = call({"command": "verify", "project_dir": str(repo), "before": before})
    assert done["ok"] is True and done["changed"] == []  # отчёт в analysis/ допустим
    write(repo / "src" / "app.py", "x = 2\n")  # плохой пример: правка кода
    bad = call({"command": "verify", "project_dir": str(repo), "before": before})
    assert bad["ok"] is False and any("src/app.py" in line for line in bad["changed"])
    write(repo / "src" / "new.py", "y = 1\n")  # новый файл вне analysis/ тоже нарушение
    assert any("new.py" in line for line in call(
        {"command": "verify", "project_dir": str(repo), "before": before}
    )["changed"])  # fmt: skip


def test_verify_ignores_changes_that_were_there_before_the_analysis(repo: Path) -> None:
    write(repo / "src" / "app.py", "x = 5\n")  # владелец оставил правку до анализа
    before = inventory(repo)["git"]["snapshot"]
    assert call({"command": "verify", "project_dir": str(repo), "before": before})["ok"] is True


def test_outside_git_verify_says_it_cannot_check(tmp_path: Path) -> None:
    facts = inventory(tmp_path)
    assert facts["git"]["is_repo"] is False and facts["git"]["clean"] is None
    done = call({"command": "verify", "project_dir": str(tmp_path), "before": None})
    assert done["ok"] is None and "не git-репозиторий" in done["note"]


@pytest.mark.parametrize(
    "payload",
    [{"command": "fix"}, {"command": "inventory", "project_dir": "/нет/такой/папки"}, {}],
)
def test_a_bad_request_is_an_error_not_a_guess(payload: dict[str, Any]) -> None:
    assert "analyze-existing" in call(payload, expect=1)["error"]


# ---------- навык ----------


def test_the_skill_is_read_only_and_promises_the_simplified_scope() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: analyze-existing\n")
    assert "disable-model-invocation: true" in text
    for fact in (
        "ничего не меняешь в коде проекта",
        "analysis/ANALYSIS_REPORT.md",
        '"command": "inventory"',
        '"command": "verify"',
        "P1-P13",
        "Что не вошло в эту версию",
        "не угадывай",
        "Не запускай тесты",
    ):
        assert fact in text, fact
