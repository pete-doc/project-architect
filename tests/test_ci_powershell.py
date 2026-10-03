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
    "# CONSTITUTION\n\n## Пороги тонкости PowerShell\n\n"
    "- max_commands: 15\n- max_loops: 0\n- max_conditions: 2\n- max_functions: 0\n"
    "- max_total_commands: 30\n- max_total_conditions: 4\n"
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


# ---------- «тонкость»: разбор штатным парсером PowerShell (AST) ----------


@pytest.fixture(scope="session")
def ps_shell() -> None:
    """Без PowerShell тесты падают, а не пропускаются (пропуск считался бы нарушением)."""
    assert SHELL is not None, "нужен PowerShell (pwsh или powershell)"


NEEDS_SHELL = pytest.mark.usefixtures("ps_shell")
LEGAL_LAUNCHER = "& python tools/run.py @args\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"


@NEEDS_SHELL
def test_a_launcher_with_an_exit_code_check_is_thin(tmp_path: Path) -> None:
    """Запуск программы и проверка кода выхода законны: условие по коду выхода допустимо."""
    project = make(tmp_path, {"scripts/run.ps1": LEGAL_LAUNCHER})
    out = passes(project, "thin")
    assert "команд" in out


# --- обход 1: блок кода в переменной ---


@NEEDS_SHELL
@pytest.mark.parametrize(
    "script",
    [
        "$f = { foreach ($i in 1..3) { Get-Item $i } }; & $f\n",
        "$f = {\n  while ($true) { break }\n}\n& $f\n",
        "$handlers = @{ run = { Get-Date } }\n& $handlers.run\n",
        "$list = @({ 1 }, { 2 })\n",
        "$f = [scriptblock]::Create('Get-Date')\n& $f\n",
    ],
)
def test_bypass_code_block_in_a_variable_is_caught(tmp_path: Path, script: str) -> None:
    project = make(tmp_path, {"scripts/run.ps1": script})
    out = fails(project, "thin")
    assert "(max_functions)" in out
    assert "scripts/run.ps1" in out


@NEEDS_SHELL
def test_a_loop_inside_a_stored_block_is_counted_as_a_loop_too(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/run.ps1": "$f = { foreach ($i in 1..3) { $i } }; & $f\n"})
    out = fails(project, "thin")
    assert "циклов 1, порог 0 (max_loops)" in out


@NEEDS_SHELL
def test_a_block_as_a_command_argument_is_not_a_stored_function(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/run.ps1": "Invoke-Command { & python tools/run.py }\n"})
    passes(project, "thin")


# --- обход 2: определение функции через диск function: ---


@NEEDS_SHELL
@pytest.mark.parametrize(
    "script",
    [
        "Set-Item function:Run { & python x.py }\n",
        "New-Item -Path function:Run -Value { & python x.py }\n",
        "$function:Run = { & python x.py }\n",
        "${function:Run} = { & python x.py }\n",
        "Set-Item -Path 'Function:Run' -Value { 1 }\n",
    ],
)
def test_bypass_function_drive_is_caught(tmp_path: Path, script: str) -> None:
    project = make(tmp_path, {"scripts/run.ps1": script})
    out = fails(project, "thin")
    assert "(max_functions)" in out
    assert "function:" in out


# --- обход 3: много команд в одной строке ---


@NEEDS_SHELL
def test_bypass_many_commands_on_one_line_is_caught(tmp_path: Path) -> None:
    line = "; ".join(f"& python step{i}.py" for i in range(16)) + "\n"
    assert line.count("\n") == 1
    project = make(tmp_path, {"scripts/run.ps1": line})
    out = fails(project, "thin")
    assert "команд 16, порог 15 (max_commands)" in out


@NEEDS_SHELL
def test_bypass_one_long_pipeline_is_caught(tmp_path: Path) -> None:
    pipeline = "Get-Item a | " + " | ".join(f"Select-Object -Property p{i}" for i in range(16))
    project = make(tmp_path, {"scripts/run.ps1": pipeline + "\n"})
    assert "(max_commands)" in fails(project, "thin")


@NEEDS_SHELL
def test_fifteen_commands_are_allowed(tmp_path: Path) -> None:
    body = "; ".join(f"& python step{i}.py" for i in range(15)) + "\n"
    passes(make(tmp_path, {"scripts/run.ps1": body}), "thin")


# --- обход 4: логика, разнесённая по нескольким маленьким скриптам ---


@NEEDS_SHELL
def test_bypass_logic_split_over_many_small_scripts_is_caught(tmp_path: Path) -> None:
    scripts = {
        f"scripts/step{i}.ps1": "".join(f"& python a{i}_{j}.py\n" for j in range(5))
        for i in range(7)
    }
    project = make(tmp_path, scripts)  # у каждого скрипта 5 команд (порог 15), всего 35 (бюджет 30)
    out = fails(project, "thin")
    assert "весь PowerShell проекта: команд 35, общий бюджет 30 (max_total_commands)" in out
    assert "max_commands" not in out  # по отдельности скрипты в норме


@NEEDS_SHELL
def test_conditions_split_over_many_scripts_hit_the_total_budget(tmp_path: Path) -> None:
    one = "& python x.py\nif ($LASTEXITCODE -ne 0) { exit 1 }\n"
    project = make(tmp_path, {f"scripts/s{i}.ps1": one for i in range(5)})
    out = fails(project, "thin")
    assert "условий 5, общий бюджет 4 (max_total_conditions)" in out


@NEEDS_SHELL
def test_scripts_within_the_total_budget_pass(tmp_path: Path) -> None:
    project = make(tmp_path, {f"scripts/s{i}.ps1": LEGAL_LAUNCHER for i in range(4)})
    passes(project, "thin")


# --- остальные меры ---


@NEEDS_SHELL
@pytest.mark.parametrize(
    "snippet",
    [
        "foreach ($i in 1..3) { $i }",
        "for ($i = 0; $i -lt 3; $i++) { $i }",
        "while ($false) { 1 }",
        "do { 1 } while ($false)",
        "do { 1 } until ($true)",
        "1..3 | ForEach-Object { $_ }",
        "1..3 | % { $_ }",
    ],
)
def test_every_kind_of_loop_is_caught(tmp_path: Path, snippet: str) -> None:
    project = make(tmp_path, {"scripts/run.ps1": LEGAL_LAUNCHER + snippet + "\n"})
    assert "(max_loops)" in fails(project, "thin")


@NEEDS_SHELL
@pytest.mark.parametrize(
    "snippet",
    [
        "if ($a) { 1 } elseif ($b) { 2 }",
        "switch ($a) { 1 { 'x' } 2 { 'y' } }",
        "Get-Item . | Where-Object { $_.Name } | Where-Object { $_.Length }",
        "Get-Item . | ? { $_.Name } | ? { $_.Length }",
    ],
)
def test_conditions_are_counted_against_a_small_limit(tmp_path: Path, snippet: str) -> None:
    project = make(tmp_path, {"scripts/run.ps1": LEGAL_LAUNCHER + snippet + "\n"})
    assert "(max_conditions)" in fails(project, "thin")


@NEEDS_SHELL
@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("function Get-Thing { 1 }", "функци"),
        ("filter Only-Big { $_ }", "функци"),
        ("workflow Run-All { }", "функци"),
        ("class Worker { }", "функци"),
        ("enum Color { Red }", "функци"),
        ("& { function Inner { 1 } }", "функци"),
        ("FUNCTION Loud { 1 }", "функци"),
        ("Invoke-Expression $text", "динамический код"),
        ("iex $text", "динамический код"),
        ("Add-Type -TypeDefinition 'public class A {}'", "динамический код"),
    ],
)
def test_own_functions_classes_and_dynamic_code_are_caught(
    tmp_path: Path, snippet: str, name: str
) -> None:
    project = make(tmp_path, {"scripts/run.ps1": LEGAL_LAUNCHER + snippet + "\n"})
    out = fails(project, "thin")
    assert "(max_functions)" in out
    assert name in out


@NEEDS_SHELL
def test_logic_words_in_comments_strings_and_parameters_are_not_logic(tmp_path: Path) -> None:
    text = (
        "# function Fake { } и foreach (1) и if ($x)\n<# class Hidden { } while ($y) #>\n"
        "Write-Output 'function Quoted { } foreach ($i in 1) { }'\n"
        "$text = @'\nfunction InText { }\nforeach ($i in 1) { }\n'@\n"
        "Get-ChildItem -Filter *.txt\n& python run.py -Function main -Foreach x\n"
    )
    passes(make(tmp_path, {"scripts/run.ps1": text}), "thin")


@NEEDS_SHELL
def test_a_script_that_does_not_parse_cannot_be_measured_and_fails(tmp_path: Path) -> None:
    project = make(tmp_path, {"scripts/run.ps1": "if ($x) {\n"})
    assert "не разбирается" in fails(project, "thin")


@NEEDS_SHELL
def test_pester_test_files_are_not_held_to_the_thin_limit(tmp_path: Path) -> None:
    project = make(
        tmp_path,
        {"scripts/run.ps1": THIN_SCRIPT, "tests/Run.Tests.ps1": "function Helper { 1 }\n" * 3},
    )
    passes(project, "thin")


@NEEDS_SHELL
def test_thresholds_come_from_the_constitution(tmp_path: Path) -> None:
    constitution = (
        "## Пороги тонкости PowerShell\n- max_commands: 4\n- max_loops: 1\n- max_conditions: 0\n"
        "- max_functions: 1\n- max_total_commands: 5\n- max_total_conditions: 0\n"
    )
    project = make(
        tmp_path, {"scripts/a.ps1": "function One { 1 }\nforeach ($i in 1) { $i }\n"}, constitution
    )
    passes(project, "thin")
    write(project / "scripts" / "a.ps1", "".join(f"& python s{i}.py\n" for i in range(5)))
    assert "команд 5, порог 4" in fails(project, "thin")
    write(project / "scripts" / "a.ps1", "function One { 1 }\nfunction Two { 2 }\n")
    assert "(max_functions)" in fails(project, "thin")


@pytest.mark.parametrize(
    "constitution",
    [
        "# без раздела\n",
        "## Пороги тонкости PowerShell\n- max_commands: 15\n",
        "## Пороги тонкости PowerShell\n" + "".join(
            f"- {k}: много\n" for k in (
                "max_commands", "max_loops", "max_conditions", "max_functions",
                "max_total_commands", "max_total_conditions",
            )
        ),
        "## Пороги тонкости PowerShell\n" + "".join(
            f"- {k}: -1\n" for k in (
                "max_commands", "max_loops", "max_conditions", "max_functions",
                "max_total_commands", "max_total_conditions",
            )
        ),
        "## Пороги тонкости PowerShell\n",
    ],
)  # fmt: skip
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
    for line in (
        "- max_commands: 15", "- max_loops: 0", "- max_conditions: 2", "- max_functions: 0",
        "- max_total_commands: 30", "- max_total_conditions: 4",
    ):  # fmt: skip
        assert line in constitution, line
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
