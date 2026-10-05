"""«Храповик» продукта применяется к самому репозиторию: удалённые и пропущенные тесты ловятся."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
REPORT_NAME = "test-report.xml"

PASSING = (
    '<testsuites><testsuite name="pytest">'
    '<testcase classname="tests.test_a" name="test_one"/>'
    '<testcase classname="tests.test_a" name="test_two"/>'
    "</testsuite></testsuites>"
)
WITH_SKIP = (
    '<testsuites><testsuite name="pytest">'
    '<testcase classname="tests.test_a" name="test_one"/>'
    '<testcase classname="tests.test_a" name="test_two"><skipped message="later"/></testcase>'
    "</testsuite></testsuites>"
)


def make_project(root: Path) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text(
        "def test_one() -> None:\n    assert True\n\n\ndef test_two() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    (root / "state").mkdir()
    return root


def run(project: Path, *args: str, report: str | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        *args,
        "--language",
        "python",
        "--project",
        str(project),
    ]
    if report is not None:
        path = project / REPORT_NAME
        path.write_text(report, encoding="utf-8")
        command += ["--report", str(path)]
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300
    )


def test_repository_ci_runs_the_ratchet_on_the_product_itself() -> None:
    # CI продукта на CircleCI (ADR-0020): полный прогон и храповик в одном задании check
    workflow = (REPO / ".circleci" / "continue_config.yml").read_text(encoding="utf-8")
    assert "--junitxml=test-report.xml" in workflow
    for check in ("tests", "skips"):
        line = f"parch_ci.py {check} --language python --report test-report.xml"
        assert line in workflow, check
    assert workflow.index("pytest -v") < workflow.index(
        "parch_ci.py tests --language python --report"
    )


def test_repository_baseline_exists_and_lists_the_product_tests() -> None:
    baseline = json.loads((REPO / "state" / "baseline.json").read_text(encoding="utf-8"))
    tests = baseline["tests"]["python"]
    assert len(tests) > 400
    assert "tests/test_ci_rules.py::test_a_broken_report_fails_the_tests_check_too[csharp]" in tests
    assert "skipped_tests" in baseline
    assert baseline["skips"]["python"] is not None


def test_only_tests_updates_the_test_lists_and_nothing_else(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    done = run(project, "baseline", "--update", "--only-tests", report=PASSING)
    assert done.returncode == 0, done.stdout + done.stderr
    baseline = json.loads((project / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["tests"]["python"] == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two",
    ]
    assert "dead_code" not in baseline or "python" not in baseline["dead_code"]
    assert "config" not in baseline or "python" not in baseline["config"]


def test_a_deleted_product_test_stops_ci(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    assert run(project, "baseline", "--update", "--only-tests", report=PASSING).returncode == 0
    (project / "tests" / "test_a.py").write_text(
        "def test_one() -> None:\n    assert True\n", encoding="utf-8"
    )
    done = run(project, "tests")
    assert done.returncode == 1
    assert "tests/test_a.py::test_two" in done.stdout


def test_a_skipped_product_test_stops_ci_even_with_an_alias(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    assert run(project, "baseline", "--update", "--only-tests", report=PASSING).returncode == 0
    (project / "tests" / "test_a.py").write_text(
        "import pytest as pt\n\n\ndef test_one() -> None:\n    assert True\n\n\n"
        "@pt.mark.skip\ndef test_two() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    done = run(project, "skips", report=WITH_SKIP)
    assert done.returncode == 1
    assert "tests.test_a::test_two" in done.stdout


def test_baseline_refuses_to_hide_a_new_skip_without_the_owner(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    assert run(project, "baseline", "--update", "--only-tests", report=PASSING).returncode == 0
    refused = run(project, "baseline", "--update", "--only-tests", report=WITH_SKIP)
    assert refused.returncode == 1
    assert "--accept-skips" in refused.stdout
    accepted = run(
        project, "baseline", "--update", "--only-tests", "--accept-skips", report=WITH_SKIP
    )
    assert accepted.returncode == 0, accepted.stdout


def test_tests_with_spaces_in_parameter_ids_are_tracked_too(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p")
    (project / "tests" / "test_a.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.parametrize('text', ['hello world', 'a b c'])\n"
        "def test_one(text: str) -> None:\n    assert text\n",
        encoding="utf-8",
    )
    report = (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_one[hello world]"/>'
        '<testcase classname="tests.test_a" name="test_one[a b c]"/>'
        "</testsuite></testsuites>"
    )
    assert run(project, "baseline", "--update", "--only-tests", report=report).returncode == 0
    baseline = json.loads((project / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["tests"]["python"] == [
        "tests/test_a.py::test_one[a b c]",
        "tests/test_a.py::test_one[hello world]",
    ]


def baseline_with_skip_lists(project: Path, **lists: list[str]) -> None:
    path = project / "state" / "baseline.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, ids in lists.items():
        data["skipped_tests"][key] = ids
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_skip_known_only_for_the_other_system_is_still_a_violation(tmp_path: Path) -> None:
    import os

    here, other = ("windows", "posix") if os.name == "nt" else ("posix", "windows")
    project = make_project(tmp_path / "p")
    assert run(project, "baseline", "--update", "--only-tests", report=PASSING).returncode == 0
    skipped = ["tests.test_a::test_two"]
    baseline_with_skip_lists(project, **{f"python@{other}": skipped})
    refused = run(project, "skips", report=WITH_SKIP)
    assert refused.returncode == 1, refused.stdout
    baseline_with_skip_lists(project, **{f"python@{here}": skipped})
    accepted = run(project, "skips", report=WITH_SKIP)
    assert accepted.returncode == 0, accepted.stdout


def test_baseline_update_records_skips_for_this_system_only(tmp_path: Path) -> None:
    import os

    project = make_project(tmp_path / "p")
    assert run(project, "baseline", "--update", "--only-tests", report=PASSING).returncode == 0
    here = "windows" if os.name == "nt" else "posix"
    baseline_with_skip_lists(project, **{"python@elsewhere": ["tests.test_a::old"]})
    done = run(project, "baseline", "--update", "--only-tests", "--accept-skips", report=WITH_SKIP)
    assert done.returncode == 0, done.stdout
    data = json.loads((project / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert data["skipped_tests"][f"python@{here}"] == ["tests.test_a::test_two"]
    assert data["skipped_tests"]["python@elsewhere"] == ["tests.test_a::old"]  # чужое не тронуто


# ---------- урок: baseline не записывается по запуску с упавшими тестами ----------

BROKEN_RUN = (
    '<testsuites><testsuite name="pytest">'
    '<testcase classname="tests.test_a" name="test_one"/>'
    '<testcase classname="tests.test_a" name="test_two"><failure message="assert 0"/></testcase>'
    "</testsuite></testsuites>"
)
ERRORED_RUN = BROKEN_RUN.replace("<failure", "<error")


@pytest.mark.parametrize("report", [BROKEN_RUN, ERRORED_RUN], ids=["failure", "error"])
def test_baseline_refuses_a_run_with_failed_tests(tmp_path: Path, report: str) -> None:
    """Цепочка команд не стоит на ошибке теста: по такому запуску baseline не пишется."""
    project = make_project(tmp_path / "p")
    done = run(project, "baseline", "--update", "--only-tests", report=report)
    assert done.returncode == 1, done.stdout
    assert "упавшие" in done.stdout
    assert "tests.test_a::test_two" in done.stdout
    assert not (project / "state" / "baseline.json").exists()


def test_baseline_refuses_failed_tests_in_typescript_and_csharp_reports(tmp_path: Path) -> None:
    ts_report = (
        '{"testResults":[{"name":"/p/a.test.ts","assertionResults":'
        '[{"status":"failed","fullName":"breaks"}]}]}'
    )
    trx_report = (
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
        '<UnitTestResult testName="App.Tests.A.Breaks" outcome="Failed"/></Results></TestRun>'
    )
    for language, text, name in (
        ("typescript", ts_report, "a.test.ts::breaks"),
        ("csharp", trx_report, "App.Tests.A.Breaks"),
    ):
        project = tmp_path / language
        (project / "state").mkdir(parents=True)
        path = project / "report.out"
        path.write_text(text, encoding="utf-8")
        done = subprocess.run(
            [
                sys.executable, str(SCRIPT), "baseline", "--update", "--language", language,
                "--project", str(project), "--report", str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=300,
        )  # fmt: skip
        assert done.returncode == 1, f"{language}: {done.stdout}"
        assert name in done.stdout, language
        assert not (project / "state" / "baseline.json").exists(), language
