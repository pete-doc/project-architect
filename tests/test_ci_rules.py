"""Правила про пропуски тестов, подавления и настройки проверок для TypeScript, C# и PowerShell.

Полный CI для этих языков появится в следующих PR фазы D, но сами правила («пропущенный тест
считается удалённым», «число подавлений не растёт», «настройки проверок защищены отпечатком»)
заложены сразу для всех четырёх языков. Здесь на каждое правило есть плохой пример.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

PROJECTS: dict[str, dict[str, str]] = {
    "csharp": {
        "src/App/App.csproj": (
            '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
            "    <Nullable>enable</Nullable>\n"
            "    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>\n"
            "  </PropertyGroup>\n</Project>\n"
        ),
        ".editorconfig": "root = true\n[*.cs]\ndotnet_diagnostic.CA1707.severity = error\n",
        "src/App/Calc.cs": (
            "namespace App;\n"
            "public static class Calc { public static int Add(int a, int b) => a + b; }\n"
        ),
        "tests/App.Tests/CalcTests.cs": (
            "using Xunit;\nnamespace App.Tests;\npublic class CalcTests\n{\n"
            "    [Fact]\n    public void Adds() => Assert.Equal(3, App.Calc.Add(1, 2));\n}\n"
        ),
    },
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
CS_TEST = "tests/App.Tests/CalcTests.cs"
CS_SRC = "src/App/Calc.cs"
PS_TEST = "tests/Tool.Tests.ps1"
PS_SRC = "src/Tool.ps1"
TS_SKIP = "it/test/describe.skip"

# (язык, проверка, файл, дописать в конец, вид нарушения в выводе)
APPENDS = [
    ("csharp", "skips", CS_TEST, '\n[Fact(Skip = "later")] void Later() {}\n', "Skip ="),
    ("csharp", "skips", CS_TEST, "\n[Ignore] void Later() {}\n", "Ignore"),
    ("csharp", "skips", CS_TEST, '\nvoid X() { Assert.Ignore("later"); }\n', "Assert.Ignore"),
    ("csharp", "suppressions", CS_SRC, "\n#pragma warning disable CS8618\n", "#pragma warning"),
    ("csharp", "suppressions", CS_SRC, '\n[SuppressMessage("a", "b")] class Z {}\n', "Suppress"),
    ("csharp", "suppressions", CS_SRC, "\n#nullable disable\n", "#nullable disable"),
    ("csharp", "suppressions", CS_SRC, "\n[ExcludeFromCodeCoverage] class Q {}\n", "ExcludeFrom"),
    ("powershell", "skips", PS_TEST, "\nDescribe 'X' { It 'later' -Skip { } }\n", "-Skip"),
    ("powershell", "skips", PS_TEST, "\nIt 'y' { Set-ItResult -Skipped }\n", "Set-ItResult"),
    ("powershell", "suppressions", PS_SRC, "\n[SuppressMessageAttribute('a')]\n", "Suppress"),
]  # fmt: skip

# (язык, файл, что заменить, на что)
SETTINGS_EDITS = [
    ("csharp", "src/App/App.csproj", "<Nullable>enable<", "<Nullable>disable<"),
    ("csharp", "src/App/App.csproj", "Errors>true<", "Errors>false<"),
    ("csharp", ".editorconfig", "severity = error", "severity = none"),
    ("powershell", "PSScriptAnalyzerSettings.psd1", "'Error', 'Warning'", "'Error'"),
]  # fmt: skip

NOWARN_PROPS = "<Project><PropertyGroup><NoWarn>CS8618</NoWarn></PropertyGroup></Project>\n"
SETTINGS_ADDITIONS = [
    ("csharp", "Directory.Build.props", NOWARN_PROPS),
    ("csharp", "global.json", '{"sdk": {"version": "1.0.0"}}\n'),
    ("powershell", "PesterConfiguration.psd1", "@{ Run = @{ Exit = $false } }\n"),
]


CLEAN_REPORTS = {
    "csharp": '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
    '<UnitTestResult testName="App.Tests.CalcTests.Adds" outcome="Passed"/></Results></TestRun>',
    "powershell": '<testsuites><testsuite name="Tool"><testcase classname="Tool" name="answers"/>'
    "</testsuite></testsuites>",
}
REPORT_FILES = {"csharp": "report.trx", "powershell": "report.xml"}


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


def test_unrelated_csproj_edits_are_free(tmp_path: Path) -> None:
    cs = make(tmp_path, "csharp")
    text = (cs.root / "src/App/App.csproj").read_text(encoding="utf-8")
    cs.write(
        "src/App/App.csproj", text.replace("</Project>", "  <ItemGroup/>" + chr(10) + "</Project>")
    )
    cs.passes("settings")


def test_skips_in_non_test_files_and_other_languages_are_not_counted(tmp_path: Path) -> None:
    project = make(tmp_path, "csharp")
    project.append(
        "src/App/Calc.cs",
        "// [Fact(Skip = " + chr(34) + "x" + chr(34) + ")] только слово" + chr(10),
    )
    project.passes("skips")  # не файл тестов
    project.write("tests/App.Tests/notes.ts", "it.skip(" + chr(39) + "x" + chr(39) + ")" + chr(10))
    project.passes("skips")  # чужое расширение


def test_checks_without_an_adapter_say_so_instead_of_passing(tmp_path: Path) -> None:
    project = make(tmp_path, "csharp")
    out = project.fails("dead-code")
    assert "ещё не реализована" in out


# ---------- пропуски по результату запуска: отчёты Vitest/Jest, dotnet test, Pester ----------

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"

# Реальные отчёты, снятые с настоящих запусков (Vitest 2.1.9, dotnet test с xUnit, Pester 3.4).
REAL_REPORTS = [
    ("csharp", "dotnet.trx", {"Demo.UnitTest1.Later", "Demo.UnitTest1.Th"}),
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


def test_a_broken_report_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    project = make(tmp_path, "csharp")
    project.report.write_text("это не XML", encoding="utf-8")
    done = project.run("skips")
    assert done.returncode != 0


def test_workflow_for_python_does_not_need_a_report_but_runs_the_tests_itself() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "--junitxml" in script  # Python: исход каждого теста берётся из отчёта pytest
    assert "REPORT_PARSERS" in script
