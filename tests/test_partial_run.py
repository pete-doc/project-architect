"""Урезанный прогон CI не даёт права на слияние: храповик отличает его от полного (ADR-0013).

Урезанный отчёт помечается именем набора `parch-partial`. Он не заменяет полный: в нём не требуют
всех тестов, но упавшие, новые пропуски и удалённые тесты останавливают его так же, как полный.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_report_gaps import FULL_PY, PY_TESTS, THREE_PY, write

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"


def partial(text: str) -> str:
    return text.replace('name="pytest"', 'name="parch-partial"')


def run(project: Path, check: str, report: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            check,
            "--project",
            str(project),
            "--report",
            str(report),
            *flags,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    write(tmp_path / "tests" / "test_a.py", PY_TESTS)
    (tmp_path / "state").mkdir()
    full = write(tmp_path / "full.xml", FULL_PY)
    done = run(tmp_path, "baseline", full, "--update", "--only-tests")
    assert done.returncode == 0, done.stdout
    return tmp_path


def test_a_marked_partial_report_passes_and_says_it_gives_no_right_to_merge(project: Path) -> None:
    report = write(project / "p.xml", partial(THREE_PY))
    for check in ("tests", "skips"):
        done = run(project, check, report, "--partial")
        assert done.returncode == 0, done.stdout
    out = run(project, "tests", report, "--partial").stdout
    assert "ЧАСТИЧНЫЙ прогон: запущено 3 из 5" in out and "права на слияние он не даёт" in out


def test_the_same_partial_report_does_not_pass_as_a_full_run(project: Path) -> None:
    report = write(project / "p.xml", partial(THREE_PY))
    for check in ("tests", "skips"):
        done = run(project, check, report)
        assert done.returncode == 1 and "помечен как урезанный" in done.stdout, check
    complete = write(project / "c.xml", partial(FULL_PY))  # даже со всеми тестами метка остаётся
    assert run(project, "tests", complete).returncode == 1


def test_partial_mode_needs_a_marked_report_so_a_full_run_cannot_hide_behind_it(
    project: Path,
) -> None:
    report = write(project / "u.xml", THREE_PY)  # без метки
    done = run(project, "tests", report, "--partial")
    assert done.returncode == 1 and "требует отчёт урезанного прогона с пометкой" in done.stdout


def test_a_failed_test_a_new_skip_and_a_removed_test_stop_a_partial_run(project: Path) -> None:
    failing = partial(
        THREE_PY.replace('name="test_one"/>', 'name="test_one"><failure/></testcase>')
    )
    done = run(project, "tests", write(project / "f.xml", failing), "--partial")
    assert done.returncode == 1 and "Упавших тестов в урезанном прогоне: 1" in done.stdout
    skipping = partial(
        THREE_PY.replace('name="test_two"/>', 'name="test_two"><skipped/></testcase>')
    )
    done = run(project, "skips", write(project / "s.xml", skipping), "--partial")
    assert done.returncode == 1 and "Пропущено по фактическому результату" in done.stdout
    (project / "tests" / "test_a.py").write_text(
        PY_TESTS.replace("def test_two", "def renamed_two"), encoding="utf-8"
    )
    done = run(project, "tests", write(project / "p.xml", partial(THREE_PY)), "--partial")
    assert done.returncode == 1 and "уменьшилось" in done.stdout


def test_baseline_is_never_updated_from_a_partial_report(project: Path) -> None:
    before = (project / "state" / "baseline.json").read_text(encoding="utf-8")
    report = write(project / "p.xml", partial(FULL_PY))
    done = run(project, "baseline", report, "--update", "--only-tests")
    assert done.returncode == 1 and "урезанный" in done.stdout
    assert (project / "state" / "baseline.json").read_text(encoding="utf-8") == before
