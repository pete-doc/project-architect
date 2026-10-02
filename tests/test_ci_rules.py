"""Правила про пропуски тестов, подавления и настройки проверок для TypeScript, C# и PowerShell.

Полный CI для этих языков появится в следующих PR фазы D, но сами правила («пропущенный тест
считается удалённым», «число подавлений не растёт», «настройки проверок защищены отпечатком»)
заложены сразу для всех четырёх языков. Здесь на каждое правило есть плохой пример.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

PROJECTS: dict[str, dict[str, str]] = {
    "typescript": {
        "package.json": json.dumps(
            {
                "name": "demo",
                "scripts": {"test": "vitest run", "lint": "eslint .", "start": "node src/a.js"},
                "devDependencies": {"vitest": "2.0.0"},
            },
            indent=2,
        ),
        "tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
        "eslint.config.js": "export default [];\n",
        "src/a.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
        "src/a.test.ts": (
            "import { it, expect } from 'vitest';\n"
            "import { add } from './a';\n\n"
            "it('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n"
        ),
    },
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
    ("typescript", "skips", TS_TEST, "\nit.skip('later', () => {});\n", TS_SKIP),
    ("typescript", "skips", TS_TEST, "\nxit('later', () => {});\n", "xit/xtest/xdescribe"),
    ("typescript", "skips", TS_TEST, "\ndescribe.only('x', () => {});\n", TS_SKIP),
    ("typescript", "skips", TS_TEST, "\ntest.todo('later');\n", TS_SKIP),
    ("typescript", "suppressions", TS_SRC, "\n// @ts-ignore\nlet x = 1;\n", "@ts-ignore"),
    ("typescript", "suppressions", TS_SRC, "\n// @ts-expect-error\nlet y = 1;\n", "@ts-ignore"),
    ("typescript", "suppressions", TS_SRC, "\n/* eslint-disable */\n", "eslint-disable"),
    ("typescript", "suppressions", TS_SRC, "\n// biome-ignore lint: x\n", "biome-ignore"),
    ("typescript", "suppressions", TS_SRC, "\n/* istanbul ignore next */\n", "coverage ignore"),
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
    ("typescript", "tsconfig.json", '"strict": true', '"strict": false'),
    ("typescript", "eslint.config.js", "export default [];", "export default [{ rules: {} }];"),
    ("typescript", "package.json", '"test": "vitest run"', '"test": "echo ok"'),
    ("typescript", "package.json", '"lint": "eslint ."', '"lint": "true"'),
    ("csharp", "src/App/App.csproj", "<Nullable>enable<", "<Nullable>disable<"),
    ("csharp", "src/App/App.csproj", "Errors>true<", "Errors>false<"),
    ("csharp", ".editorconfig", "severity = error", "severity = none"),
    ("powershell", "PSScriptAnalyzerSettings.psd1", "'Error', 'Warning'", "'Error'"),
]  # fmt: skip

NOWARN_PROPS = "<Project><PropertyGroup><NoWarn>CS8618</NoWarn></PropertyGroup></Project>\n"
SETTINGS_ADDITIONS = [
    ("typescript", ".jscpd.json", '{"threshold": 100}\n'),
    ("typescript", "biome.json", '{"linter": {"enabled": false}}\n'),
    ("typescript", "vitest.config.ts", "export default { test: { exclude: ['**/*'] } };\n"),
    ("csharp", "Directory.Build.props", NOWARN_PROPS),
    ("csharp", "global.json", '{"sdk": {"version": "1.0.0"}}\n'),
    ("powershell", "PesterConfiguration.psd1", "@{ Run = @{ Exit = $false } }\n"),
]


class Project:
    def __init__(self, root: Path, language: str) -> None:
        self.root = root
        self.language = language
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
                "--language", self.language, "--project", str(self.root), *flags,
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


def test_unrelated_package_json_and_csproj_edits_are_free(tmp_path: Path) -> None:
    ts = make(tmp_path, "typescript")
    text = (ts.root / "package.json").read_text(encoding="utf-8")
    ts.write(
        "package.json", text.replace('"name": "demo"', '"name": "demo2"').replace("2.0.0", "2.1.0")
    )
    ts.passes("settings")
    cs = make(tmp_path, "csharp")
    text = (cs.root / "src/App/App.csproj").read_text(encoding="utf-8")
    cs.write("src/App/App.csproj", text.replace("</Project>", "  <ItemGroup/>\n</Project>"))
    cs.passes("settings")


def test_skips_in_non_test_files_and_other_languages_are_not_counted(tmp_path: Path) -> None:
    project = make(tmp_path, "typescript")
    project.append("src/a.ts", "\nconst note = 'it.skip is only a word here';\n")
    project.passes("skips")  # не файл тестов
    project.write("src/b.cs", '[Fact(Skip = "x")] class B {}\n')
    project.passes("skips")  # чужое расширение


def test_checks_without_an_adapter_say_so_instead_of_passing(tmp_path: Path) -> None:
    project = make(tmp_path, "typescript")
    out = project.fails("dead-code")
    assert "ещё не реализована" in out
