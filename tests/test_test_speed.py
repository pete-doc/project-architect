"""Скорость тестов: быстрый режим по умолчанию, полный отдельно, baseline без запуска тестов.

Не меняет сами проверки: тесты здесь следят, что режимы не разъехались и что медленные тесты
(dotnet, pwsh, node, PSScriptAnalyzer) действительно помечены.
"""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
FAST = ["-p", "no:cacheprovider", "-n", "0", "--collect-only", "-q"]


def config() -> dict[str, object]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    ini: dict[str, object] = data["tool"]["pytest"]["ini_options"]
    return ini


def collect(*args: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", *FAST, *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]
    return [line for line in done.stdout.splitlines() if "::" in line]


# ---------- режимы ----------


def test_the_default_run_is_parallel_and_skips_slow_tests() -> None:
    addopts = str(config()["addopts"])
    assert "-n auto" in addopts
    assert "--dist loadgroup" in addopts
    assert "-m 'not slow'" in addopts


def test_the_slow_marker_and_the_xdist_group_marker_are_declared() -> None:
    markers = " ".join(str(m) for m in config()["markers"])  # type: ignore[attr-defined]
    assert "slow:" in markers and "xdist_group:" in markers


def test_the_full_run_has_more_tests_than_the_fast_one_and_the_difference_is_the_slow_set() -> None:
    fast = collect("tests")
    full = collect("tests", "-m", "")
    slow = collect("tests", "-m", "slow")
    assert len(full) == len(fast) + len(slow)
    assert len(slow) > 150
    assert set(fast).isdisjoint(slow)


def test_tests_that_run_dotnet_pwsh_node_or_the_analyzer_are_slow() -> None:
    slow = set(collect("tests", "-m", "slow"))
    fast = set(collect("tests"))
    must_be_slow = [
        "tests/test_ci_csharp.py::test_clean_project_passes_every_check",
        "tests/test_ci_csharp.py::test_dead_code_is_found[IDE0051]",
        "tests/test_ci_typescript.py::test_clean_project_passes_every_check_and_every_real_tool",
        "tests/test_ci_powershell.py::test_wrong_psscriptanalyzer_version_is_an_error_not_a_pass",
        "tests/test_ci_powershell.py::test_the_tests_run_powershell_7_and_not_windows_powershell",
        "tests/test_ci_powershell.py::test_the_analyzer_check_reports_that_it_ran_on_powershell_7",
    ]
    for test_id in must_be_slow:
        assert test_id in slow, test_id
        assert test_id not in fast, test_id


def test_cheap_text_checks_stay_in_the_fast_run() -> None:
    fast = set(collect("tests"))
    for test_id in (
        "tests/test_ci_csharp.py::test_workflow_pins_every_version_and_runs_on_ubuntu",
        "tests/test_ci_powershell.py::test_ps_lexer_hash_inside_a_word_is_not_a_comment",
        "tests/test_standard_check.py::test_a_workflow_with_every_required_setting_passes",
    ):
        assert test_id in fast, test_id


def test_slow_tests_of_one_language_share_one_xdist_group() -> None:
    script = (
        "import json, sys, pytest\n"
        "class Rec:\n"
        "    def __init__(self): self.rows = {}\n"
        "    @pytest.hookimpl(trylast=True)\n"
        "    def pytest_collection_modifyitems(self, items):\n"
        "        for i in items:\n"
        "            m = i.get_closest_marker('xdist_group')\n"
        "            self.rows[i.nodeid] = [bool(i.get_closest_marker('slow')),\n"
        "                                    m.args[0] if m else None]\n"
        "rec = Rec()\n"
        "pytest.main(['-p', 'no:cacheprovider', '-n', '0', '--collect-only', '-q', '-m', '', "
        "'tests/test_ci_csharp.py', 'tests/test_ci_typescript.py', 'tests/test_ci_powershell.py'],"
        " plugins=[rec])\n"
        "print('ROWS' + json.dumps(rec.rows))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    payload = next(x for x in done.stdout.splitlines() if x.startswith("ROWS"))
    rows: dict[str, list[object]] = json.loads(payload[4:])
    expected = {
        "test_ci_csharp": "csharp",
        "test_ci_typescript": "typescript",
        "test_ci_powershell": "powershell",
    }
    for node, (is_slow, group) in rows.items():
        module = Path(node.split("::")[0]).stem
        assert group == (expected[module] if is_slow else None), node
    assert {g for _, g in rows.values() if g} == set(expected.values())


# ---------- baseline без запуска тестов ----------


def project(root: Path, extra_ini: str = "") -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "tests" / "test_a.py").write_text(
        "import pathlib\nimport pytest\n\n\n"
        "def test_fast() -> None:\n    pathlib.Path('ran-fast.txt').write_text('x')\n\n\n"
        "@pytest.mark.slow\n"
        "def test_slow() -> None:\n    pathlib.Path('ran-slow.txt').write_text('x')\n",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    slow: медленный\naddopts = -m 'not slow'\n" + extra_ini,
        encoding="utf-8",
    )
    return root


def run_parch(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--language", "python", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


def test_the_test_count_includes_slow_tests_despite_the_default_filter(tmp_path: Path) -> None:
    root = project(tmp_path)
    done = run_parch(root, "tests")
    assert done.returncode == 0, done.stdout
    assert "Тестов: 2" in done.stdout
    assert not (root / "ran-fast.txt").exists()  # сбор не запускает тесты


def test_baseline_only_tests_never_runs_the_tests_itself(tmp_path: Path) -> None:
    root = project(tmp_path)
    done = run_parch(root, "baseline", "--update", "--only-tests")
    assert done.returncode == 1
    assert "нужен --report test-report.xml" in done.stdout
    assert "gh run download" in done.stdout
    assert not (root / "ran-fast.txt").exists()
    assert not (root / "ran-slow.txt").exists()
    assert not (root / "state" / "baseline.json").exists()


def test_baseline_only_tests_takes_the_count_from_collection_and_skips_from_the_report(
    tmp_path: Path,
) -> None:
    root = project(tmp_path)
    report = tmp_path / "test-report.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_fast"/>'
        '<testcase classname="tests.test_a" name="test_slow"><skipped message="later"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    refused = run_parch(root, "baseline", "--update", "--only-tests", "--report", str(report))
    assert refused.returncode == 0, refused.stdout  # первый baseline: пропуск ещё не известен
    data = json.loads((root / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert data["tests"]["python"] == ["tests/test_a.py::test_fast", "tests/test_a.py::test_slow"]
    assert any("test_slow" in x for v in data["skipped_tests"].values() for x in v)
    assert not (root / "ran-fast.txt").exists()
    assert not (root / "ran-slow.txt").exists()


@pytest.mark.slow
def test_a_group_really_runs_on_one_worker_under_xdist() -> None:
    """Без tryfirst на хуке меток xdist их не видит, и тесты группы расходятся по процессам."""
    done = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/test_ci_powershell.py", "-m", "", "-n", "3",
            "-v", "-p", "no:cacheprovider", "-k", "findings_fail or information_level",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
    )  # fmt: skip
    workers = set(re.findall(r"\[(gw\d+)\] \[ *\d+%\] PASSED", done.stdout))
    assert done.returncode == 0, done.stdout[-1500:]
    assert len(workers) == 1, f"тесты группы разошлись по процессам: {sorted(workers)}"


def test_xdist_group_suffix_in_a_report_is_not_part_of_the_test_name(tmp_path: Path) -> None:
    """Под --dist loadgroup имя в отчёте выглядит как `test_x[p]@csharp`: ключи должны совпасть."""
    root = project(tmp_path)
    report = tmp_path / "test-report.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_fast@csharp"/>'
        '<testcase classname="tests.test_a" name="test_slow@typescript"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    done = run_parch(root, "tests", "--report", str(report))
    assert done.returncode == 0, done.stdout
    from parch_ci import junit_outcomes

    assert set(junit_outcomes(report)) == {"tests.test_a::test_fast", "tests.test_a::test_slow"}


def test_report_platform_decides_where_the_skips_are_recorded(tmp_path: Path) -> None:
    root = project(tmp_path)
    report = tmp_path / "test-report.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_fast"/>'
        '<testcase classname="tests.test_a" name="test_slow"><skipped message="posix only"/>'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    args = ["baseline", "--update", "--only-tests", "--report", str(report)]
    assert run_parch(root, *args, "--report-platform", "posix").returncode == 0
    data = json.loads((root / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert data["skipped_tests"]["python@posix"] == ["tests.test_a::test_slow"]
    assert "python@windows" not in data["skipped_tests"]
    # на Windows тот же пропуск остаётся нарушением: для этой системы он не известен
    skips = run_parch(root, "skips", "--report", str(report))
    expected = 0 if sys.platform != "win32" else 1
    assert skips.returncode == expected, skips.stdout


# ---------- документы и CI ----------


def test_agents_md_explains_both_modes_and_the_baseline_flow() -> None:
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "до 3 минут" in text and "до 15 минут" in text
    assert "`pytest`" in text and 'pytest -m ""' in text
    assert "--report-platform windows" in text and "test-report" in text  # baseline локально
    assert "posix" in text and "Перед каждой отправкой" in text
    assert "запрещён" in text and "ci/circleci: check" in text
    assert "Array buffer allocation failed" in text and "не обходи" in text
    assert len(text.splitlines()) <= 150


def test_product_ci_runs_the_full_mode_and_uploads_the_report() -> None:
    # CI продукта на CircleCI (ADR-0020): отчёт сохраняется шагами store_* и при падении тестов
    workflow = (REPO / ".circleci" / "continue_config.yml").read_text(encoding="utf-8")
    assert 'pytest -v -n 4 -m "" --junitxml=test-report.xml' in workflow
    assert re.search(r"- store_artifacts:\n\s+when: always\n\s+path: test-report.xml", workflow)
    assert re.search(r"- store_test_results:\n\s+when: always\n\s+path: test-report.xml", workflow)


def test_the_python_template_runs_the_tests_once_and_reuses_the_report() -> None:
    template = (REPO / "plugin" / "templates" / "ci" / "circleci" / "python.yml").read_text(
        encoding="utf-8"
    )
    assert "timeout 600 pytest -v --junitxml=test-report.xml" in template
    assert "parch_ci.py tests --report test-report.xml" in template
    assert "parch_ci.py skips --report test-report.xml" in template


def test_xdist_is_pinned_exactly() -> None:
    lines = (REPO / "requirements-dev.txt").read_text(encoding="utf-8").split()
    assert any(re.fullmatch(r"pytest-xdist==\d+\.\d+\.\d+", line) for line in lines)
