"""Тест из кода или из baseline, которого нет в отчёте запуска, считается не запущенным.

Урезанный отчёт (скажем, 3 теста из 916) не должен проходить ни `tests`, ни `skips`: иначе
пропуск или удаление теста можно спрятать, просто убрав его из отчёта.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"


def run(
    project: Path, check: str, language: str, report: Path, *flags: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT), check, "--language", language,
            "--project", str(project), "--report", str(report), *flags,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )  # fmt: skip


def passes(project: Path, check: str, language: str, report: Path) -> str:
    done = run(project, check, language, report)
    assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
    return done.stdout


def fails(project: Path, check: str, language: str, report: Path) -> str:
    done = run(project, check, language, report)
    assert done.returncode == 1, f"{check} должен был упасть: {done.stdout}{done.stderr}"
    assert "ПРОВАЛ" in done.stdout
    return done.stdout


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_baseline(project: Path, language: str, tests: list[str]) -> None:
    data: dict[str, object] = {
        "version": 1,
        "tests": {language: tests},
        "skipped_tests": {language: []},
    }
    write(project / "state" / "baseline.json", json.dumps(data))


# ---------- Python ----------

PY_TESTS = (
    "import pytest\n\n\n"
    "def test_one() -> None:\n    assert True\n\n\n"
    "def test_two() -> None:\n    assert True\n\n\n"
    "class TestGroup:\n    def test_three(self) -> None:\n        assert True\n\n\n"
    "@pytest.mark.parametrize('text', ['a b', 'c'])\n"
    "def test_four(text: str) -> None:\n    assert text\n"
)


def junit(*names: tuple[str, str]) -> str:
    cases = "".join(f'<testcase classname="{c}" name="{n}"/>' for c, n in names)
    return f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>'


FULL_PY = junit(
    ("tests.test_a", "test_one"),
    ("tests.test_a", "test_two"),
    ("tests.test_a.TestGroup", "test_three"),
    ("tests.test_a", "test_four[a b]"),
    ("tests.test_a", "test_four[c]"),
)
THREE_PY = junit(
    ("tests.test_a", "test_one"),
    ("tests.test_a", "test_two"),
    ("tests.test_a.TestGroup", "test_three"),
)


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    write(tmp_path / "tests" / "test_a.py", PY_TESTS)
    (tmp_path / "state").mkdir()
    return tmp_path


def test_python_full_report_passes_both_checks(py_project: Path) -> None:
    report = write(py_project / "report.xml", FULL_PY)
    passes(py_project, "tests", "python", report)
    passes(py_project, "skips", "python", report)


def test_python_truncated_report_fails_tests_and_skips(py_project: Path) -> None:
    report = write(py_project / "report.xml", THREE_PY)
    for check in ("tests", "skips"):
        out = fails(py_project, check, "python", report)
        assert "tests/test_a.py::test_four[a b]" in out, check
        assert "не запущенным" in out, check


def test_python_report_without_a_known_test_fails(py_project: Path) -> None:
    full = write(py_project / "full.xml", FULL_PY)
    done = run(py_project, "baseline", "python", full, "--update", "--only-tests")
    assert done.returncode == 0, done.stdout
    short = write(py_project / "short.xml", THREE_PY)
    assert "tests/test_a.py::test_four[c]" in fails(py_project, "tests", "python", short)


def test_python_baseline_update_refuses_a_truncated_report(py_project: Path) -> None:
    report = write(py_project / "report.xml", THREE_PY)
    done = run(py_project, "baseline", "python", report, "--update", "--only-tests")
    assert done.returncode == 1
    assert "не запущенным" in done.stdout
    assert not (py_project / "state" / "baseline.json").exists()


def test_a_three_test_report_does_not_pass_against_the_product_baseline(tmp_path: Path) -> None:
    """Тот самый случай: в baseline продукта сотни тестов, в отчёте три."""
    (tmp_path / "state").mkdir()
    shutil.copyfile(REPO / "state" / "baseline.json", tmp_path / "state" / "baseline.json")
    write(tmp_path / "tests" / "test_a.py", PY_TESTS)
    report = write(tmp_path / "report.xml", THREE_PY)
    assert "уменьшилось" in fails(tmp_path, "tests", "python", report)
    assert "отчёте запуска нет" in fails(tmp_path, "skips", "python", report)


@pytest.mark.parametrize(
    ("test_id", "key"),
    [
        ("tests/test_a.py::test_one", "tests.test_a::test_one"),
        ("tests/test_a.py::TestGroup::test_three", "tests.test_a.TestGroup::test_three"),
        ("tests/sub/test_b.py::test_x[a b]", "tests.sub.test_b::test_x[a b]"),
        ("tests\\sub\\test_b.py::test_x", "tests.sub.test_b::test_x"),
        ("tests/test_a.py::test_y[x::z/w]", "tests.test_a::test_y[x::z/w]"),
        ("tests/test_a.py::A::B::test_n", "tests.test_a.A.B::test_n"),
    ],
)
def test_pytest_ids_are_matched_to_junit_keys(test_id: str, key: str) -> None:
    from parch_ci import py_report_key

    assert py_report_key(test_id) == key


# ---------- TypeScript ----------


def ts_report(*items: tuple[str, str]) -> str:
    files: dict[str, list[dict[str, str]]] = {}
    for file, name in items:
        files.setdefault(file, []).append({"status": "passed", "fullName": name})
    results = [{"name": f"/p/{f}", "assertionResults": r} for f, r in files.items()]
    return json.dumps({"testResults": results})


TS_FILE = "import { it } from 'vitest';\n\nit('one', () => {});\nit('two', () => {});\n"


def test_typescript_truncated_report_fails_tests_and_skips(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "a.test.ts", TS_FILE)
    write_baseline(tmp_path, "typescript", ["a.test.ts::one", "a.test.ts::two"])
    full = write(tmp_path / "full.json", ts_report(("a.test.ts", "one"), ("a.test.ts", "two")))
    passes(tmp_path, "tests", "typescript", full)
    passes(tmp_path, "skips", "typescript", full)
    short = write(tmp_path / "short.json", ts_report(("a.test.ts", "one")))
    for check in ("tests", "skips"):
        assert "a.test.ts::two" in fails(tmp_path, check, "typescript", short), check


def test_typescript_test_file_missing_from_the_report_fails_without_a_baseline(
    tmp_path: Path,
) -> None:
    write(tmp_path / "tests" / "a.test.ts", TS_FILE)
    write(tmp_path / "tests" / "b.test.ts", TS_FILE)
    report = write(tmp_path / "r.json", ts_report(("a.test.ts", "one")))
    for check in ("tests", "skips"):
        out = fails(tmp_path, check, "typescript", report)
        assert "b.test.ts" in out, check
        assert "a.test.ts (" not in out, check


def test_typescript_helper_file_without_tests_is_not_a_gap(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "a.test.ts", TS_FILE)
    write(tmp_path / "tests" / "helpers.test.ts", "export const x = 1; // it('x') только слова\n")
    report = write(tmp_path / "r.json", ts_report(("a.test.ts", "one")))
    passes(tmp_path, "tests", "typescript", report)


# ---------- C# ----------

CS_TESTS = (
    "using Xunit;\n\nnamespace App.Tests;\n\n"
    "public class OrderTests\n{\n    [Fact]\n    public void Places() { }\n}\n\n"
    "public class PricingTests\n{\n    [Theory]\n    [InlineData(1)]\n"
    "    public void Rounds(int x) { }\n}\n\n"
    'public static class Helpers\n{\n    public const string Text = "[Fact]";\n}\n'
)


def trx(*names: str) -> str:
    results = "".join(f'<UnitTestResult testName="{n}" outcome="Passed"/>' for n in names)
    return (
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
        f"<Results>{results}</Results></TestRun>"
    )


def test_csharp_truncated_report_fails_tests_and_skips(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "App.Tests" / "OrderTests.cs", CS_TESTS)
    names = ["App.Tests.OrderTests.Places", "App.Tests.PricingTests.Rounds(x: 1)"]
    write_baseline(tmp_path, "csharp", names)
    full = write(tmp_path / "full.trx", trx(*names))
    passes(tmp_path, "tests", "csharp", full)
    passes(tmp_path, "skips", "csharp", full)
    short = write(tmp_path / "short.trx", trx(names[0]))
    for check in ("tests", "skips"):
        assert "PricingTests.Rounds" in fails(tmp_path, check, "csharp", short), check


def test_csharp_test_class_missing_from_the_report_fails_without_a_baseline(
    tmp_path: Path,
) -> None:
    write(tmp_path / "tests" / "App.Tests" / "OrderTests.cs", CS_TESTS)
    report = write(tmp_path / "r.trx", trx("App.Tests.OrderTests.Places"))
    for check in ("tests", "skips"):
        out = fails(tmp_path, check, "csharp", report)
        assert "класс PricingTests" in out, check
        assert "Helpers" not in out, check  # класс без тестов не считается


def test_csharp_report_with_all_classes_passes_without_a_baseline(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "App.Tests" / "OrderTests.cs", CS_TESTS)
    report = write(
        tmp_path / "r.trx",
        trx("App.Tests.OrderTests.Places", "App.Tests.PricingTests.Rounds(x: 1.5)"),
    )
    passes(tmp_path, "tests", "csharp", report)
    passes(tmp_path, "skips", "csharp", report)


def test_a_removal_approved_by_the_owner_passes_the_not_run_check(py_project: Path) -> None:
    full = write(py_project / "full.xml", FULL_PY)
    assert run(py_project, "baseline", "python", full, "--update", "--only-tests").returncode == 0
    write(
        py_project / "tests" / "test_a.py",
        "def test_one() -> None:\n    assert True\n\n\ndef test_two() -> None:\n    assert True\n",
    )
    report = write(
        py_project / "after.xml", junit(("tests.test_a", "test_one"), ("tests.test_a", "test_two"))
    )
    refused = run(py_project, "baseline", "python", report, "--update", "--only-tests")
    assert refused.returncode == 1
    assert "--accept-removed" in refused.stdout
    accepted = run(
        py_project, "baseline", "python", report, "--update", "--only-tests", "--accept-removed"
    )
    assert accepted.returncode == 0, accepted.stdout
    passes(py_project, "tests", "python", report)
