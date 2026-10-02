"""Правила про пропуски тестов, подавления и настройки проверок для PowerShell.

TypeScript и C# проверяются в своих файлах (test_ci_typescript.py, test_ci_csharp.py).

Полный CI для PowerShell появится в последнем PR фазы D, но сами правила («пропущенный тест
считается удалённым», «число подавлений не растёт», «настройки проверок защищены отпечатком»)
заложены сразу для всех языков. Здесь на каждое правило есть плохой пример.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

PROJECTS: dict[str, dict[str, str]] = {
    "powershell": {
        "PSScriptAnalyzerSettings.psd1": "@{ Severity = @('Error', 'Warning') }\n",
        "src/Tool.ps1": "function Get-Answer { 42 }\n",
        "tests/Tool.Tests.ps1": (
            "Describe 'Tool' {\n    It 'answers' {\n"
            "        (Get-Answer) | Should -Be 42\n    }\n}\n"
        ),
    },
}

TS_TEST = "src/a.test.ts"
TS_SRC = "src/a.ts"
PS_TEST = "tests/Tool.Tests.ps1"
PS_SRC = "src/Tool.ps1"
TS_SKIP = "it/test/describe.skip"

# (язык, проверка, файл, дописать в конец, вид нарушения в выводе)
APPENDS = [
    ("powershell", "skips", PS_TEST, "\nDescribe 'X' { It 'later' -Skip { } }\n", "-Skip"),
    ("powershell", "skips", PS_TEST, "\nIt 'y' { Set-ItResult -Skipped }\n", "Set-ItResult"),
    ("powershell", "suppressions", PS_SRC, "\n[SuppressMessageAttribute('a')]\n", "Suppress"),
]  # fmt: skip

# (язык, файл, что заменить, на что)
SETTINGS_EDITS = [
    ("powershell", "PSScriptAnalyzerSettings.psd1", "'Error', 'Warning'", "'Error'"),
]  # fmt: skip

NOWARN_PROPS = "<Project><PropertyGroup><NoWarn>CS8618</NoWarn></PropertyGroup></Project>\n"
SETTINGS_ADDITIONS = [
    ("powershell", "PesterConfiguration.psd1", "@{ Run = @{ Exit = $false } }\n"),
]


CLEAN_REPORTS = {
    "powershell": '<testsuites><testsuite name="Tool"><testcase classname="Tool" name="answers"/>'
    "</testsuite></testsuites>",
}
REPORT_FILES = {"powershell": "report.xml"}


class Project:
    def __init__(self, root: Path, language: str) -> None:
        self.root = root
        self.language = language
        self.report: Path = root.parent / f"{language}-{REPORT_FILES[language]}"
        self.report.write_text(CLEAN_REPORTS[language], encoding="utf-8")
        for rel, text in PROJECTS[language].items():
            self.write(rel, text)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def append(self, rel: str, text: str) -> None:
        with (self.root / rel).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def run(self, check: str, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), check,
                "--language", self.language, "--project", str(self.root),
                "--report", str(self.report), *flags,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=300,
        )  # fmt: skip

    def passes(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
        assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
        return done.stdout

    def fails(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
        assert done.returncode == 1, f"{check} должен был упасть: {done.stdout}{done.stderr}"
        return done.stdout


def make(tmp_path: Path, language: str) -> Project:
    project = Project(tmp_path / language, language)
    project.passes("baseline", "--update")
    return project


@pytest.mark.parametrize("language", list(PROJECTS))
def test_clean_project_passes_the_three_checks(tmp_path: Path, language: str) -> None:
    project = make(tmp_path, language)
    for check in ("skips", "suppressions", "settings"):
        project.passes(check)


@pytest.mark.parametrize(("language", "check", "rel", "text", "expected"), APPENDS)
def test_new_skips_and_suppressions_are_violations(
    tmp_path: Path, language: str, check: str, rel: str, text: str, expected: str
) -> None:
    project = make(tmp_path, language)
    project.append(rel, text)
    out = project.fails(check)
    assert expected in out
    flag = "--accept-skips" if check == "skips" else "--accept-suppressions"
    assert flag in out
    refused = project.fails("baseline", "--update")
    assert flag in refused
    project.passes("baseline", "--update", flag)
    project.passes(check)  # решение владельца принято: новый baseline


@pytest.mark.parametrize(("language", "rel", "old", "new"), SETTINGS_EDITS)
def test_changing_check_settings_is_blocked(
    tmp_path: Path, language: str, rel: str, old: str, new: str
) -> None:
    project = make(tmp_path, language)
    text = (project.root / rel).read_text(encoding="utf-8")
    assert old in text
    project.write(rel, text.replace(old, new, 1))
    out = project.fails("settings")
    assert rel in out
    assert "--accept-config" in out
    assert "--accept-config" in project.fails("baseline", "--update")
    project.passes("baseline", "--update", "--accept-config")
    project.passes("settings")


@pytest.mark.parametrize(("language", "rel", "content"), SETTINGS_ADDITIONS)
def test_adding_a_settings_file_is_blocked(
    tmp_path: Path, language: str, rel: str, content: str
) -> None:
    project = make(tmp_path, language)
    project.write(rel, content)
    assert rel in project.fails("settings")


# ---------- пропуски по результату запуска: отчёты Vitest/Jest, dotnet test, Pester ----------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"

# Реальные отчёты, снятые с настоящих запусков (Vitest 2.1.9, dotnet test с xUnit, Pester 3.4).
REAL_REPORTS = [
    ("powershell", "pester-nunit.xml", {"Tool.skipped", "Tool.pending", "Tool.inconclusive"}),
]  # fmt: skip


@pytest.mark.parametrize(("language", "fixture", "skipped"), REAL_REPORTS)
def test_parsers_read_real_test_reports(language: str, fixture: str, skipped: set[str]) -> None:
    from parch_ci import REPORT_PARSERS, skipped_ids

    outcomes = REPORT_PARSERS[language](FIXTURES / fixture)
    assert skipped_ids(outcomes) == skipped
    assert "passed" in outcomes.values()  # исходы «прошёл» тоже разобраны


def test_real_vitest_report_marks_it_fails_as_passed_so_the_text_search_matters() -> None:
    """В отчёте Vitest `it.fails` выглядит как «прошёл»: его ловит поиск по тексту."""
    from parch_ci import REPORT_PARSERS

    outcomes = REPORT_PARSERS["typescript"](FIXTURES / "vitest.json")
    assert outcomes["a.test.js::known"] == "passed"


@pytest.mark.parametrize(("language", "fixture", "skipped"), REAL_REPORTS)
def test_skips_from_the_real_run_are_violations_until_the_owner_accepts(
    tmp_path: Path, language: str, fixture: str, skipped: set[str]
) -> None:
    project = make(tmp_path, language)
    clean = project.report
    project.report = FIXTURES / fixture
    out = project.fails("skips")
    assert "по фактическому результату запуска" in out
    for test_id in sorted(skipped):
        assert test_id in out
    refused = project.fails("baseline", "--update")
    assert "--accept-skips" in refused
    project.passes("baseline", "--update", "--accept-skips")
    assert "все известны" in project.passes("skips")
    project.report = clean  # вернулись к чистому отчёту: это улучшение, а не нарушение
    project.passes("skips")


@pytest.mark.parametrize("language", list(PROJECTS))
def test_skips_without_a_report_fail_instead_of_passing_on_text_search_alone(
    tmp_path: Path, language: str
) -> None:
    project = make(tmp_path, language)
    done = subprocess.run(
        [
            sys.executable, str(SCRIPT), "skips",
            "--language", language, "--project", str(project.root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )  # fmt: skip
    assert done.returncode == 1
    assert "Нет отчёта о запуске тестов" in done.stdout
    assert "--report" in done.stdout


def test_workflow_for_python_does_not_need_a_report_but_runs_the_tests_itself() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "--junitxml" in script  # Python: исход каждого теста берётся из отчёта pytest
    assert "REPORT_PARSERS" in script


# ---------- повреждённый отчёт это провал, а не «тестов нет» ----------

BROKEN_REPORTS = {
    "не XML и не JSON": "это не отчёт",
    "пустой файл": "",
    "чужой XML": "<html><body>ошибка</body></html>",
    "пустой JSON": "{}",
    "отчёт без тестов (XML)": "<testsuites></testsuites>",
}
# формат отчёта на язык: TRX (C#), JSON Vitest/Jest, Pester (PowerShell), JUnit (Python)
REPORT_LANGUAGES = {
    "csharp": "dotnet.trx",
    "typescript": "vitest.json",
    "powershell": "pester-nunit.xml",
    "python": None,
}


def run_check(
    check: str, language: str, project: Path, report: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT), check, "--language", language,
            "--project", str(project), "--report", str(report),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )  # fmt: skip


@pytest.mark.parametrize("language", sorted(REPORT_LANGUAGES))
@pytest.mark.parametrize("damage", sorted(BROKEN_REPORTS))
def test_a_broken_report_fails_the_skips_check_in_every_format(
    tmp_path: Path, language: str, damage: str
) -> None:
    report = tmp_path / "report.out"
    report.write_text(BROKEN_REPORTS[damage], encoding="utf-8")
    done = run_check("skips", language, tmp_path, report)
    assert done.returncode == 1, f"{language}/{damage}: {done.stdout}{done.stderr}"
    assert "ПРОВАЛ" in done.stdout
    assert "Traceback" not in done.stderr


@pytest.mark.parametrize("language", [x for x in sorted(REPORT_LANGUAGES) if x != "python"])
def test_a_truncated_real_report_fails_the_skips_check(tmp_path: Path, language: str) -> None:
    fixture = REPORT_LANGUAGES[language]
    assert fixture is not None
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    report = tmp_path / "report.out"
    report.write_text(text[: len(text) // 2], encoding="utf-8")
    done = run_check("skips", language, tmp_path, report)
    assert done.returncode == 1, f"{language}: {done.stdout}{done.stderr}"
    assert "ПРОВАЛ" in done.stdout


@pytest.mark.parametrize("language", ["csharp", "typescript"])
def test_a_broken_report_fails_the_tests_check_too(tmp_path: Path, language: str) -> None:
    report = tmp_path / "report.out"
    report.write_text("это не отчёт", encoding="utf-8")
    done = run_check("tests", language, tmp_path, report)
    assert done.returncode == 1
    assert "ПРОВАЛ" in done.stdout


@pytest.mark.parametrize("language", [x for x in sorted(REPORT_LANGUAGES) if x != "python"])
def test_real_reports_still_parse_after_the_strict_checks(language: str) -> None:
    from parch_ci import REPORT_PARSERS

    fixture = REPORT_LANGUAGES[language]
    assert fixture is not None
    assert REPORT_PARSERS[language](FIXTURES / fixture)


# ---------- параметризованный тест с пустым набором не должен молча пропускаться ----------


def test_pytest_is_configured_to_fail_on_an_empty_parameter_set() -> None:
    import tomllib

    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["empty_parameter_set_mark"] == "fail_at_collect"


def run_pytest_on_empty_parameter_set(folder: Path, ini: str) -> subprocess.CompletedProcess[str]:
    (folder / "test_empty.py").write_text(
        "import pytest\n\n\n"
        "@pytest.mark.parametrize('x', [][1:])\n"
        "def test_never_runs(x: int) -> None:\n"
        "    assert x\n",
        encoding="utf-8",
    )
    (folder / "pytest.ini").write_text("[pytest]\n" + ini, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(folder)],
        cwd=folder,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def test_a_parametrized_test_with_no_parameters_fails_instead_of_being_skipped(
    tmp_path: Path,
) -> None:
    import tomllib

    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    value = config["tool"]["pytest"]["ini_options"]["empty_parameter_set_mark"]
    guarded = tmp_path / "guarded"
    guarded.mkdir()
    done = run_pytest_on_empty_parameter_set(guarded, f"empty_parameter_set_mark={value}\n")
    assert done.returncode != 0, done.stdout
    assert "empty parameter set" in (done.stdout + done.stderr).lower()
    # контроль: без настройки pytest молча пропускает такой тест (поэтому настройка нужна)
    plain = tmp_path / "plain"
    plain.mkdir()
    control = run_pytest_on_empty_parameter_set(plain, "")
    assert control.returncode == 0, control.stdout
    assert "skipped" in control.stdout


def test_every_parameter_list_in_the_suite_is_not_empty() -> None:
    """Страховка для списков-констант: пустой список параметров это ошибка теста."""
    assert REAL_REPORTS
    assert list(PROJECTS)
    assert BROKEN_REPORTS
    assert APPENDS
    assert SETTINGS_EDITS
    assert SETTINGS_ADDITIONS
