"""Облегчённый CI для PowerShell (ADR-0010): PSScriptAnalyzer, «тонкость», подавления, настройки.

PowerShell в проектах только запускает программы. Здесь на каждое правило есть заведомо плохой
пример; PSScriptAnalyzer запускается настоящий, той версии, что в CI-шаблоне.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
WORKFLOW = REPO / "plugin" / "templates" / "ci" / "powershell.yml"
TEMPLATE_SETTINGS = REPO / "plugin" / "templates" / "powershell" / "PSScriptAnalyzerSettings.psd1"
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reports"
PSA_VERSION = "1.25.0"
SHELL = shutil.which("pwsh") or shutil.which("powershell")

CONSTITUTION = (
    "# CONSTITUTION\n\n## Пороги тонкости PowerShell\n\n- max_lines: 40\n- max_functions: 0\n"
)
THIN_SCRIPT = "& python tools/run.py @args\nexit $LASTEXITCODE\n"


def psa_installed() -> bool:
    if SHELL is None:
        return False
    command = (
        "(Get-Module -ListAvailable -Name PSScriptAnalyzer | "
        f"Where-Object {{ $_.Version -eq [version]'{PSA_VERSION}' }}).Count"
    )
    done = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return done.stdout.strip() not in ("", "0")


@pytest.fixture(scope="session")
def psa() -> None:
    """Без PSScriptAnalyzer тесты падают, а не пропускаются (пропуск считался бы нарушением)."""
    assert psa_installed(), (
        f"нужны PowerShell и PSScriptAnalyzer {PSA_VERSION}: "
        f"Install-Module PSScriptAnalyzer -RequiredVersion {PSA_VERSION} -Scope CurrentUser"
    )


NEEDS_PSA = pytest.mark.usefixtures("psa")


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def make(
    root: Path, scripts: dict[str, str] | None = None, constitution: str | None = None
) -> Path:
    write(root / "docs" / "CONSTITUTION.md", CONSTITUTION if constitution is None else constitution)
    shutil.copyfile(TEMPLATE_SETTINGS, root / "PSScriptAnalyzerSettings.psd1")
    (root / "state").mkdir(exist_ok=True)
    for rel, text in (scripts if scripts is not None else {"scripts/run.ps1": THIN_SCRIPT}).items():
        write(root / rel, text)
    return root


def run(project: Path, check: str, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(SCRIPT), check, "--language", "powershell",
            "--project", str(project), *flags,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )  # fmt: skip


def passes(project: Path, check: str, *flags: str) -> str:
    done = run(project, check, *flags)
    assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
    return done.stdout


def fails(project: Path, check: str, *flags: str) -> str:
    done = run(project, check, *flags)
    assert done.returncode == 1, f"{check} должен был упасть: {done.stdout}{done.stderr}"
    assert "ПРОВАЛ" in done.stdout
    return done.stdout


# ---------- PSScriptAnalyzer ----------


@NEEDS_PSA
def test_a_thin_launcher_passes_every_powershell_check(tmp_path: Path) -> None:
    project = make(tmp_path)
    for check in ("psscriptanalyzer", "thin", "suppressions", "settings"):
        passes(project, check)


@NEEDS_PSA
@pytest.mark.parametrize(
    ("snippet", "rule"),
    [
        ("Write-Host 'x'", "PSAvoidUsingWriteHost"),
        ("Invoke-Expression $command", "PSAvoidUsingInvokeExpression"),
        ("gci .", "PSAvoidUsingCmdletAliases"),
        ("try { Get-Item x } catch { }", "PSAvoidUsingEmptyCatchBlock"),
        ("$unused = 1", "PSUseDeclaredVarsMoreThanAssignments"),
        ("if ($x) {", "MissingEndCurlyBrace"),
    ],
)
def test_psscriptanalyzer_findings_fail_the_check(tmp_path: Path, snippet: str, rule: str) -> None:
    project = make(tmp_path, {"scripts/run.ps1": THIN_SCRIPT + snippet + "\n"})
    out = fails(project, "psscriptanalyzer")
    assert rule in out
    assert "scripts/run.ps1" in out


@NEEDS_PSA
def test_every_script_is_analysed_not_only_the_first(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/a.ps1": THIN_SCRIPT, "tools/b.ps1": "Write-Host 'x'\n"})
    out = fails(project, "psscriptanalyzer")
    assert "tools/b.ps1" in out


@NEEDS_PSA
def test_information_level_findings_do_not_fail(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/run.ps1": "param($a)\n& python x.py $a\n"})
    passes(project, "psscriptanalyzer")


def test_psscriptanalyzer_with_no_scripts_says_so_instead_of_pretending(tmp_path: Path) -> None:
    project = make(tmp_path, {})
    assert "Скриптов PowerShell пока нет" in passes(project, "psscriptanalyzer")


def test_wrong_psscriptanalyzer_version_is_an_error_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import parch_ci

    assert SHELL is not None, "нужен PowerShell"

    project = make(tmp_path)
    monkeypatch.setattr(parch_ci, "PSA_VERSION", "9.9.9")
    with pytest.raises(parch_ci.ToolError, match="нужен PSScriptAnalyzer 9.9.9"):
        parch_ci.psa_findings(project, [project / "scripts" / "run.ps1"])


def test_other_languages_cannot_run_powershell_checks(tmp_path: Path) -> None:
    for check in ("psscriptanalyzer", "thin"):
        done = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                check,
                "--language",
                "python",
                "--project",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert done.returncode == 1
        assert "только к PowerShell" in done.stdout


# ---------- «тонкость» ----------


def test_script_over_the_line_limit_fails_with_a_hint(tmp_path: Path) -> None:
    long = "".join(f"& python step{i}.py\n" for i in range(41))
    project = make(tmp_path, {"scripts/long.ps1": long})
    out = fails(project, "thin")
    assert "scripts/long.ps1: строк кода 41, порог 40" in out
    assert "Перенесите логику в Python или C#" in out


def test_script_at_the_limit_passes(tmp_path: Path) -> None:
    project = make(
        tmp_path, {"scripts/edge.ps1": "".join(f"& python s{i}.py\n" for i in range(40))}
    )
    passes(project, "thin")


def test_blank_lines_and_comments_are_not_counted(tmp_path: Path) -> None:
    body = "# комментарий\n\n<# блок\nкомментариев #>\n" * 30 + THIN_SCRIPT
    project = make(tmp_path, {"scripts/commented.ps1": body})
    passes(project, "thin")


def test_here_string_content_counts_as_code_lines(tmp_path: Path) -> None:
    """Код, спрятанный в here-строку (для Invoke-Expression), всё равно считается."""
    hidden = "$code = @'\n" + "".join(f"Get-Item {i}\n" for i in range(45)) + "'@\n"
    project = make(tmp_path, {"scripts/hidden.ps1": hidden})
    assert "строк кода" in fails(project, "thin")


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("function Get-Thing { 1 }", "function Get-Thing"),
        ("filter Only-Big { $_ }", "filter Only-Big"),
        ("workflow Run-All { }", "workflow Run-All"),
        ("class Worker { }", "class Worker"),
        ("enum Color { Red }", "enum Color"),
        ("& { function Inner { 1 } }", "function Inner"),
        ("FUNCTION Loud { 1 }", "FUNCTION Loud"),
    ],
)
def test_own_functions_and_classes_fail_the_thin_check(
    tmp_path: Path, snippet: str, name: str
) -> None:
    project = make(tmp_path, {"scripts/run.ps1": THIN_SCRIPT + snippet + "\n"})
    out = fails(project, "thin")
    assert "собственных функций и классов" in out
    assert name in out


def test_function_words_in_comments_strings_and_parameters_are_not_functions(
    tmp_path: Path,
) -> None:
    text = (
        "# function Fake { }\n<# class Hidden { } #>\n"
        "Write-Output 'function Quoted { }'\n"
        "Get-ChildItem -Filter *.txt\n& python run.py -Function main\n"
    )
    project = make(tmp_path, {"scripts/run.ps1": text})
    passes(project, "thin")


def test_pester_test_files_are_not_held_to_the_thin_limit(tmp_path: Path) -> None:
    project = make(
        tmp_path,
        {"scripts/run.ps1": THIN_SCRIPT, "tests/Run.Tests.ps1": "function Helper { 1 }\n" * 3},
    )
    passes(project, "thin")


def test_thresholds_come_from_the_constitution(tmp_path: Path) -> None:
    constitution = "## Пороги тонкости PowerShell\n- max_lines: 2\n- max_functions: 1\n"
    body = "function One { 1 }\n& python a.py\n"
    project = make(tmp_path, {"scripts/a.ps1": body}, constitution)
    passes(project, "thin")
    write(project / "scripts" / "a.ps1", body + "& python b.py\n")
    assert "строк кода 3, порог 2" in fails(project, "thin")
    write(project / "scripts" / "a.ps1", "function One { 1 }\nfunction Two { 2 }\n")
    assert "порог 1" in fails(project, "thin")


@pytest.mark.parametrize(
    "constitution",
    [
        "# без раздела\n",
        "## Пороги тонкости PowerShell\n- max_lines: 40\n",
        "## Пороги тонкости PowerShell\n- max_lines: много\n- max_functions: 0\n",
        "## Пороги тонкости PowerShell\n- max_lines: -1\n- max_functions: 0\n",
        "## Пороги тонкости PowerShell\n",
    ],
)
def test_missing_or_broken_thresholds_fail_instead_of_disabling_the_check(
    tmp_path: Path, constitution: str
) -> None:
    project = make(tmp_path, constitution=constitution)
    out = fails(project, "thin")
    assert "Пороги тонкости PowerShell" in out


def test_thin_check_without_a_constitution_fails(tmp_path: Path) -> None:
    write(tmp_path / "scripts" / "run.ps1", THIN_SCRIPT)
    assert "нет CONSTITUTION.md" in fails(tmp_path, "thin")


def test_thin_check_without_scripts_says_so(tmp_path: Path) -> None:
    project = make(tmp_path, {})
    assert "Скриптов PowerShell пока нет" in passes(project, "thin")


# ---------- подавления и настройки ----------

SUPPRESS = "[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '')]\n"


def test_a_new_psscriptanalyzer_suppression_is_a_violation(tmp_path: Path) -> None:
    project = make(tmp_path)
    passes(project, "baseline", "--update")
    write(project / "scripts" / "run.ps1", SUPPRESS + THIN_SCRIPT)
    out = fails(project, "suppressions")
    assert "SuppressMessage" in out
    assert "--accept-suppressions" in out


def test_a_comment_form_of_suppression_is_counted_too(tmp_path: Path) -> None:
    project = make(tmp_path)
    passes(project, "baseline", "--update")
    write(project / "scripts" / "run.ps1", THIN_SCRIPT + "# PSScriptAnalyzer disable\n")
    assert "PSScriptAnalyzer disable" in fails(project, "suppressions")


def test_suppression_words_in_strings_and_comments_are_not_counted(tmp_path: Path) -> None:
    project = make(tmp_path)
    passes(project, "baseline", "--update")
    text = THIN_SCRIPT + "Write-Output 'SuppressMessageAttribute( и # PSScriptAnalyzer'\n"
    write(project / "scripts" / "run.ps1", text + "# SuppressMessageAttribute('x') в комментарии\n")
    passes(project, "suppressions")


def test_old_suppression_passes_and_one_more_does_not(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/run.ps1": SUPPRESS + THIN_SCRIPT})
    passes(project, "baseline", "--update")
    passes(project, "suppressions")
    write(project / "scripts" / "run.ps1", SUPPRESS + SUPPRESS + THIN_SCRIPT)
    assert "было 1, стало 2" in fails(project, "suppressions")
    assert "--accept-suppressions" in fails(project, "baseline", "--update")
    passes(project, "baseline", "--update", "--accept-suppressions")
    passes(project, "suppressions")


def test_baseline_update_for_powershell_needs_no_pester_report(tmp_path: Path) -> None:
    project = make(tmp_path)
    out = passes(project, "baseline", "--update")
    assert "baseline обновлён" in out
    data = json.loads((project / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert "PSScriptAnalyzerSettings.psd1" in data["config"]["powershell"]


def test_weakening_the_analyzer_settings_is_caught(tmp_path: Path) -> None:
    project = make(tmp_path)
    passes(project, "baseline", "--update")
    settings = project / "PSScriptAnalyzerSettings.psd1"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "IncludeDefaultRules = $true",
            "IncludeDefaultRules = $true\n    ExcludeRules = @('PSAvoidUsingWriteHost')",
        ),
        encoding="utf-8",
    )
    out = fails(project, "settings")
    assert "PSScriptAnalyzerSettings.psd1" in out
    assert "--accept-config" in out


@pytest.mark.parametrize(
    "check", ["tests", "modules", "deps", "dead-code", "duplicates", "architecture", "coverage"]
)
def test_checks_that_do_not_apply_to_powershell_say_so_and_fail(tmp_path: Path, check: str) -> None:
    out = fails(make(tmp_path), check)
    assert "не применяется (ADR-0010" in out


# ---------- парсер отчётов Pester остаётся ----------


def test_the_pester_report_parser_is_kept_and_still_works() -> None:
    from parch_ci import REPORT_PARSERS, skipped_ids

    outcomes = REPORT_PARSERS["powershell"](FIXTURES / "pester-nunit.xml")
    assert skipped_ids(outcomes) == {"Tool.skipped", "Tool.pending", "Tool.inconclusive"}


# ---------- разбор PowerShell ----------


def test_ps_lexer_blanks_comments_and_strings() -> None:
    from parch_ci import blank_ps_comments_and_strings

    code, comments = blank_ps_comments_and_strings(
        "Write-Output 'a # b' # note one\n"
        '$x = "q `" # still string" # note two\n'
        "<# block\nover lines #> Get-Item y\n"
        "$h = @'\nfunction Inside { }\n'@\n"
        "$u = 'it''s' # tail\n"
    )
    assert "function" not in code
    assert "note" not in code
    assert "Get-Item y" in code
    assert "note one" in comments and "note two" in comments and "block" in comments
    assert code.count("\n") == 8


def test_ps_lexer_hash_inside_a_word_is_not_a_comment() -> None:
    from parch_ci import blank_ps_comments_and_strings

    code, comments = blank_ps_comments_and_strings("Get-Item a#b\n")
    assert "a#b" in code
    assert comments == ""


def test_ps_lexer_can_keep_strings_for_counting_lines() -> None:
    from parch_ci import blank_ps_comments_and_strings

    code, _ = blank_ps_comments_and_strings("$h = @'\nline one\nline two\n'@\n", keep_strings=True)
    assert "line one" in code


# ---------- CI-шаблон и /parch:init-project ----------


def test_workflow_pins_the_analyzer_and_runs_on_windows() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "windows-latest" in text
    assert f"-RequiredVersion {PSA_VERSION}" in text
    for check in ("psscriptanalyzer", "thin", "suppressions", "settings"):
        assert f"parch_ci.py {check} --language powershell" in text
    for absent in ("pester", "coverage", "duplicates", "architecture", "@latest"):
        assert absent not in text.lower()
    script = SCRIPT.read_text(encoding="utf-8")
    assert f'PSA_VERSION = "{PSA_VERSION}"' in script


def run_init(project: Path, languages: list[str]) -> dict[str, object]:
    request = {
        "project_dir": str(project),
        "name": "Мой сервис",
        "languages": languages,
        "description": "Запускает программы.",
        "priorities": "надёжность",
    }
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env=os.environ,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    result: dict[str, object] = json.loads(done.stdout.decode("utf-8"))
    return result


def test_init_creates_a_powershell_project_with_thresholds(tmp_path: Path) -> None:
    run_init(tmp_path / "ps", ["powershell"])
    root = tmp_path / "ps"
    for rel in (
        "PSScriptAnalyzerSettings.psd1", "state/baseline.json", ".github/parch/parch_ci.py",
        ".github/workflows/ci.yml", "docs/CONSTITUTION.md",
    ):  # fmt: skip
        assert (root / rel).is_file(), rel
    assert (root / ".github" / "workflows" / "ci.yml").read_text("utf-8") == WORKFLOW.read_text(
        "utf-8"
    )
    constitution = (root / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert "## Пороги тонкости PowerShell" in constitution
    assert "- max_lines: 40" in constitution
    assert "- max_functions: 0" in constitution
    assert "- psgallery: PSScriptAnalyzer" in constitution
    assert "Pester" not in constitution
    baseline = json.loads((root / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert "PSScriptAnalyzerSettings.psd1" in baseline["config"]["powershell"]
    assert "powershell" not in baseline["tests"]


@NEEDS_PSA
def test_generated_powershell_project_is_green_and_catches_a_fat_script(tmp_path: Path) -> None:
    run_init(tmp_path / "ps", ["powershell"])
    root = tmp_path / "ps"
    write(root / "scripts" / "run.ps1", THIN_SCRIPT)
    for check in ("psscriptanalyzer", "thin", "suppressions", "settings"):
        passes(root, check)
    write(root / "scripts" / "run.ps1", THIN_SCRIPT + "function Helper { 1 }\n")
    assert "Helper" in fails(root, "thin")


def test_python_and_powershell_together_get_separate_workflows(tmp_path: Path) -> None:
    run_init(tmp_path / "both", ["python", "powershell"])
    workflows = sorted(p.name for p in (tmp_path / "both" / ".github" / "workflows").iterdir())
    assert workflows == ["ci-powershell.yml", "ci.yml"]
    constitution = (tmp_path / "both" / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert constitution.count("## Пороги тонкости PowerShell") == 1


def test_product_ci_installs_the_same_analyzer_version_as_the_template() -> None:
    product = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"-RequiredVersion {PSA_VERSION}" in product
