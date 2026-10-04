# ruff: noqa: E501
"""«Проверить перед отправкой» одной командой (docs/LESSONS.md): порядок, остановка, правки только текста."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "preflight.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("preflight_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight: Any = load()


def titles(steps: list[tuple[str, list[str]]]) -> list[str]:
    return [title for title, _ in steps]


# ---------- порядок и остановка ----------


def test_a_code_change_runs_ruff_format_pyright_standard_and_the_full_run_in_that_order() -> None:
    steps = preflight.plan(True, "py")
    assert titles(steps) == [
        "ruff check",
        "ruff format --check",
        "pyright",
        "standard",
        "полный прогон тестов",
    ]
    full = steps[-1][1]
    assert (
        full[:3] == ["py", "-m", "pytest"] and "" in full and "--junitxml=test-report.xml" in full
    )


def test_a_text_only_change_runs_nothing_but_standard() -> None:
    assert titles(preflight.plan(False, "py")) == ["standard"]


def test_the_run_stops_at_the_first_failing_step_and_returns_its_code() -> None:
    steps = preflight.plan(True, "py")
    calls: list[list[str]] = []
    said: list[str] = []

    def execute(argv: list[str]) -> int:
        calls.append(argv)
        return 3 if "pyright" in argv else 0

    code = preflight.run(steps, execute, said.append)
    assert code == 3
    assert [c[2] if len(c) > 2 else c[1] for c in calls][-1] == "pyright"
    assert len(calls) == 3  # ruff check, ruff format, pyright; дальше не идёт
    assert any("ОСТАНОВКА" in line and "pyright" in line for line in said)
    assert not any("можно отправлять" in line for line in said)


def test_a_green_run_executes_every_step_once_and_says_it_can_be_sent() -> None:
    steps = preflight.plan(True, "py")
    seen: list[list[str]] = []
    said: list[str] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    assert preflight.run(steps, execute, said.append) == 0
    assert seen == [argv for _, argv in steps]
    assert said[-1].startswith("Всё прошло")


def test_the_first_step_failing_runs_no_later_step() -> None:
    seen: list[list[str]] = []

    def execute(argv: list[str]) -> int:
        seen.append(argv)
        return 1

    def ignore(_: str) -> None:
        return None

    assert preflight.run(preflight.plan(True, "py"), execute, ignore) == 1
    assert len(seen) == 1


@pytest.mark.parametrize("system", ["windows", "posix"])
def test_the_baseline_step_names_the_platform_of_the_report(system: str) -> None:
    title, argv = preflight.baseline_step("py", system)
    assert title == "baseline"
    assert argv[argv.index("--report-platform") + 1] == system
    assert (
        "--only-tests" in argv and "--accept-removed" not in argv
    )  # удаления только с решением владельца


# ---------- что считается правкой только текста ----------


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True, timeout=60,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".github").mkdir()
    shutil.copy(REPO / ".github" / "scope.py", tmp_path / ".github" / "scope.py")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "BACKLOG.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_edits_to_text_files_alone_do_not_need_tests(repo: Path) -> None:
    (repo / "docs" / "BACKLOG.md").write_text("y\n", encoding="utf-8")
    (repo / "docs" / "new-note.md").write_text(
        "новая заметка\n", encoding="utf-8"
    )  # неотслеживаемый
    assert preflight.code_changed(repo) is False


def test_any_code_file_or_rule_file_needs_the_full_run(repo: Path) -> None:
    (repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert preflight.code_changed(repo) is True
    git(repo, "checkout", "--", "src/app.py")
    (repo / "docs" / "GOAL.md").write_text("цель\n", encoding="utf-8")  # читают проверки
    assert preflight.code_changed(repo) is True


def test_when_nothing_is_known_the_full_run_is_chosen(repo: Path, tmp_path: Path) -> None:
    assert preflight.code_changed(repo) is True  # нет изменений: проверять осторожно
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert preflight.code_changed(outside) is True  # git не ответил


# ---------- журнал ошибок, AGENTS.md, заготовка F15 ----------


def test_lessons_log_follows_the_rule_and_links_the_closed_automation() -> None:
    text = (REPO / "docs" / "LESSONS.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 50
    assert "## Журнал процессных ошибок ИИ" in text and "## Закрыто автоматикой" in text
    assert "На втором повторе обязательна автоматика" in text
    assert "| Ошибка | Повторов | Последний раз | Автоматика |" in text
    closed = text.split("## Закрыто автоматикой", 1)[1]
    assert "scripts/preflight.py" in closed and "tests/test_preflight.py" in closed
    template = (REPO / "plugin" / "templates" / "docs" / "LESSONS.md").read_text(encoding="utf-8")
    assert "## Журнал процессных ошибок ИИ" in template and "## Закрыто автоматикой" in template


def test_agents_md_points_to_the_single_command() -> None:
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count("python scripts/preflight.py") >= 2
    assert "ruff format --check .\npyright" not in agents  # перечня команд больше нет
    assert (REPO / "scripts" / "preflight.py").is_file()


def test_the_f15_stub_keeps_every_owner_requirement() -> None:
    spec = (REPO / "docs" / "specs" / "F15-analyze-existing.md").read_text(encoding="utf-8")
    assert "заготовка, не утверждена" in spec
    for fact in (
        "Каталог возможностей",
        "проверяет его свежесть",
        "однострочное описание публичных функций",
        "Проверяет линтер",
        "по уровням",
        "только в одном модуле",
        "Протокол из 5 шагов",
        "раздел «Основания» в PR",
    ):
        assert fact.lower() in spec.lower(), fact
    features = json.loads((REPO / "state" / "features.json").read_text(encoding="utf-8"))[
        "features"
    ]
    f15 = next(f for f in features if f["id"] == "F15")
    assert f15["spec"] == "docs/specs/F15-analyze-existing.md"
