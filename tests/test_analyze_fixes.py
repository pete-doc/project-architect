# ruff: noqa: E501
"""F15, PR 1b: исправления навыка по живому прогону на fm26-data (восемь ошибок, найденных настоящими инструментами).

Каждая ошибка имеет тест, который на старом коде падал.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from test_analyze_core import analyze, deadcode, history, report, tools
from test_analyze_existing import call, git, write

RUFF_OUTPUT = (
    "a.py:1:1: UP031 [*] Use format specifiers\n"
    "a.py:2:1: E501 Line too long\n"
    "Found 2731 errors.\n"
    "[*] 1758 fixable with the `--fix` option (50 hidden fixes can be enabled with the `--unsafe-fixes` option).\n"
)


def fake_runner(
    answers: dict[str, tuple[int, str]] | None = None,
) -> tuple[list[list[str]], Any]:
    calls: list[list[str]] = []

    def runner(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
        calls.append(argv)
        for needle, (code, out) in (answers or {}).items():
            if needle in " ".join(argv):
                return code, out, ""
        return 0, "", ""

    return calls, runner


def nowhere(name: str) -> str | None:
    return None


def everything(name: str) -> str | None:
    return f"/bin/{name}"


# ---------- 1. dotnet по каждому проекту, а не из корня ----------


def test_dotnet_runs_once_per_csproj_when_the_root_has_no_solution(tmp_path: Path) -> None:
    rel = ["src/A/A.csproj", "src/B/B.csproj", "src/A/Class1.cs", "obj/x/Old.csproj"]
    calls, runner = fake_runner()
    results = tools.run_baseline(
        tmp_path, ["dotnet-build"], {"csharp": 3}, runner, everything, rel=rel
    )
    targets = [c[2] for c in calls]
    assert targets == ["src/A/A.csproj", "src/B/B.csproj"]  # корень и obj/ не собираются
    assert results[0]["metrics"]["projects"] == 2 and results[0]["metrics"]["built"] == 2


def test_a_solution_wins_over_loose_projects_and_no_project_means_skipped(tmp_path: Path) -> None:
    assert tools.dotnet_targets(["x/A.csproj", "All.sln"]) == ["All.sln"]
    results = tools.run_baseline(
        tmp_path, ["dotnet-format"], {"csharp": 1}, fake_runner()[1], everything, rel=["a.cs"]
    )
    assert results[0]["status"] == "пропущен: нет .sln и .csproj"


def test_post_build_steps_stop_the_build_until_the_owner_decides(tmp_path: Path) -> None:
    write(
        tmp_path / "P" / "P.csproj",
        '<Project><Target Name="T" AfterTargets="Build"><Copy SourceFiles="a" DestinationFolder="C:/Game" /></Target></Project>\n',
    )
    calls, runner = fake_runner()
    rel = ["P/P.csproj"]
    stopped = tools.run_baseline(
        tmp_path, ["dotnet-build"], {"csharp": 1}, runner, everything, rel=rel
    )
    assert (
        stopped[0]["status"].startswith("не запускался: в проектах есть шаги после сборки")
        and not calls
    )
    allowed = tools.run_baseline(
        tmp_path,
        ["dotnet-build"],
        {"csharp": 1},
        runner,
        everything,
        rel=rel,
        allow_build_steps=True,
    )
    assert allowed[0]["status"] == "выполнен" and calls


# ---------- 2. карточка по языкам без смешения ----------


def card_values(baseline: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    rows = report.health_card(
        {"python": 3, "csharp": 2}, {}, [], {"total": 0, "shown": []}, baseline, 4
    )
    return {(r["language"], r["signal"]): r["value"] for r in rows}


def test_the_card_keeps_each_language_to_its_own_tools() -> None:
    ruff: dict[str, Any] = {
        "tool": "ruff",
        "status": "выполнен",
        "metrics": {"issues": 10, "auto_fixable": 7, "top_rules": []},
    }
    build = {
        "tool": "dotnet-build",
        "status": "выполнен",
        "metrics": {"projects": 2, "built": 1, "failed": ["B.csproj"], "warnings": 3, "errors": 1},
    }
    values = card_values([ruff, build])
    assert "ruff" in values[("python", "Ошибки типов и линтера")]
    assert "ruff" not in values[("csharp", "Ошибки типов и линтера")]
    assert "не собрались: B.csproj" in values[("csharp", "Сборка и тесты проходят")]
    assert (
        values[("python", "Сборка и тесты проходят")] == "не измерялось"
    )  # чужая упавшая сборка не попадает в Python


def test_the_card_counts_dead_code_and_hotspots_per_language() -> None:
    hot = [{"path": "a.py", "has_test": False}, {"path": "b.cs", "has_test": True}]
    dead: dict[str, Any] = {"total": 3, "shown": [], "by_language": {"python": 2, "csharp": 1}}
    rows = report.health_card({"python": 1, "csharp": 1}, {}, hot, dead, [], 0)
    values = {(r["language"], r["signal"]): r["value"] for r in rows}
    assert values[("python", "Мёртвый код")] == "кандидатов в мёртвый код: 2"
    assert values[("csharp", "Горячие точки")] == "горячих точек без теста: 0 из 1"


# ---------- 3. вывод инструментов разбирается в числа ----------


def test_ruff_output_becomes_numbers_with_the_autofixable_count_separate() -> None:
    parsed = tools.parse_ruff(RUFF_OUTPUT)
    assert parsed["issues"] == 2731 and parsed["auto_fixable"] == 1758
    assert parsed["top_rules"][0]["rule"] in {"UP031", "E501"}
    assert tools.parse_ruff("All checks passed!\n") == {
        "issues": 0,
        "auto_fixable": 0,
        "top_rules": [],
    }


def test_the_card_shows_autofixable_ruff_issues_separately_and_names_the_rule_set() -> None:
    result = {"tool": "ruff", "status": "выполнен", "metrics": tools.parse_ruff(RUFF_OUTPUT)}
    text = card_values([result])[("python", "Ошибки типов и линтера")]
    assert "2731 замечаний, из них исправляется автоматически: 1758" in text
    assert "правилам стандарта (без настроек проекта)" in text


def test_pyright_json_is_read_even_with_noise_before_it() -> None:
    out = 'npm warn something\n{"summary": {"filesAnalyzed": 9, "errorCount": 282, "warningCount": 4}}\n'
    assert tools.parse_pyright(out) == {"errors": 282, "warnings": 4, "files": 9}
    assert tools.parse_pyright("not json") == {}


def test_dotnet_build_and_format_outputs_become_numbers() -> None:
    assert tools.parse_build("    6 Warning(s)\n    1 Error(s)\n", 1) == {
        "ok": False,
        "warnings": 6,
        "errors": 1,
    }
    no_summary = (
        "A.cs(1,1): warning CS8618: x\nA.cs(1,1): warning CS8618: x\nB.cs(2,2): warning CS0168: y\n"
    )
    assert tools.parse_build(no_summary, 0)["warnings"] == 2  # одинаковые строки считаются один раз
    fmt = "a/B.cs(3,1): warning WHITESPACE: fix\na/B.cs(4,1): error FINALNEWLINE: fix\na/C.cs(1,1): warning IDE0055: fix\n"
    assert tools.parse_format(fmt) == {"violations": 3, "files": 2}


def test_psscriptanalyzer_counts_by_severity_and_skips_protected_zones(tmp_path: Path) -> None:
    lines = f"{tmp_path / 'ops' / 'a.ps1'}|3|Warning|PSAvoidUsingWriteHost\n{tmp_path / 'data' / 'b.ps1'}|1|Error|PSX\n{tmp_path / 'ops' / 'c.ps1'}|2|Information|PSY\n"
    parsed = tools.parse_psa(lines, ["data"], tmp_path)
    assert (parsed["errors"], parsed["warnings"], parsed["information"]) == (0, 1, 1)


def test_jscpd_report_becomes_percentages_by_format(tmp_path: Path) -> None:
    formats = {
        "python": {"lines": 600, "duplicatedLines": 75, "clones": 3, "percentage": 12.5},
        "csharp": {"lines": 400, "duplicatedLines": 5, "clones": 1, "percentage": 1.25},
    }
    write(tmp_path / "jscpd-report.json", json.dumps({"statistics": {"formats": formats}}))
    parsed = tools.parse_jscpd(tmp_path)
    assert parsed["by_format"]["python"]["percentage"] == 12.5
    assert parsed["lines"] == 1000 and parsed["percentage"] == 8.0 and parsed["clones"] == 4
    assert tools.parse_jscpd(tmp_path / "нет") == {}


def test_psscriptanalyzer_is_started_with_an_encoded_command_that_survives_quoting() -> None:
    import base64

    argv = next(t for t in tools.RUNNABLE if t.key == "psscriptanalyzer").argv
    assert argv[-2] == "-EncodedCommand" and "-Command" not in argv
    script = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert "Invoke-ScriptAnalyzer -Path . -Recurse" in script and "OutputEncoding" in script


def test_powershell_and_tsc_get_no_stray_dot_argument(tmp_path: Path) -> None:
    calls, runner = fake_runner()
    langs = {"powershell": 1, "typescript": 1, "python": 1}
    tools.run_baseline(
        tmp_path, ["psscriptanalyzer", "tsc", "pyright"], langs, runner, everything, rel=[]
    )
    by_exe = {c[0]: c for c in calls}
    assert by_exe["/bin/pwsh"][-2] == "-EncodedCommand" and len(by_exe["/bin/pwsh"]) == 5
    assert by_exe["/bin/tsc"] == ["/bin/tsc", "--noEmit"]
    assert by_exe["/bin/pyright"][-1] == "."


def test_an_error_text_instead_of_findings_is_not_counted_as_zero_findings(tmp_path: Path) -> None:
    assert tools.parse_psa("ForEach-Object: cannot bind parameter", [], tmp_path) == {}
    assert tools.parse_psa("", [], tmp_path)["warnings"] == 0  # пустой вывод — действительно чисто
    assert tools.parse_ruff("error: unexpected argument") == {}
    failed = [("a.csproj", 1, "MSBUILD : error MSB1009: Project file does not exist")]
    assert tools.summarize("dotnet-format", failed, [], tmp_path, tmp_path) == {}


def test_an_unparsed_output_is_flagged_on_the_card_not_hidden() -> None:
    result: dict[str, Any] = {"tool": "pyright", "status": "выполнен", "metrics": {}}
    assert "вывод не разобрался" in card_values([result])[("python", "Ошибки типов и линтера")]


# ---------- 4. write не затирает состояние «до» ----------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path / "src" / "a.py", "def used() -> int:\n    return 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "c0")
    return tmp_path


def test_write_keeps_the_saved_before_state_so_verify_still_sees_a_change(repo: Path) -> None:
    call({"command": "inventory", "project_dir": str(repo)})
    write(repo / "src" / "a.py", "def used() -> int:\n    return 2\n")  # нарушение после inventory
    call({"command": "write", "project_dir": str(repo)})
    bad = call({"command": "verify", "project_dir": str(repo)})
    assert bad["ok"] is False and any("a.py" in line for line in bad["changed"])


# ---------- 5. повторный анализ в новую папку ----------


def test_a_second_analysis_goes_to_a_new_folder_next_to_the_first(repo: Path) -> None:
    call({"command": "inventory", "project_dir": str(repo)})
    call({"command": "write", "project_dir": str(repo)})
    again = {"project_dir": str(repo), "report_dir": "parch-analysis-2"}
    call({"command": "inventory", **again})
    out = call({"command": "write", **again})
    assert all(name.startswith("parch-analysis-2/") for name in out["written"])
    assert (repo / "parch-analysis-2" / ".parch-analysis").is_file()
    assert call({"command": "verify", **again})["ok"] is True
    write(repo / "parch-analysis" / "QUESTIONS.md", "правка старого отчёта\n")
    assert (
        call({"command": "verify", **again})["ok"] is False
    )  # чужая для этого прогона папка не разрешена


@pytest.mark.parametrize(
    "name", ["../x", "parch-analysis/x", "analysis", "parch-analysis 2", "parch-analysis-\\x"]
)
def test_a_bad_report_folder_name_is_refused(repo: Path, name: str) -> None:
    out = call({"command": "inventory", "project_dir": str(repo), "report_dir": name}, expect=1)
    assert "не подходит" in out["error"]
    assert sorted(p.name for p in repo.iterdir()) == [".git", "src"]


def test_old_analysis_folders_do_not_inflate_name_counts_or_notes(repo: Path) -> None:
    write(repo / "parch-analysis" / ".parch-analysis", "x\n")
    write(repo / "parch-analysis" / "QUESTIONS.md", "unique_orphan_name нигде\n")
    write(repo / "src" / "b.py", "def unique_orphan_name() -> None: ...\n")
    git(repo, "add", "src")
    git(repo, "commit", "-q", "-m", "c1")
    facts = analyze.inventory(repo, [], report_dir="parch-analysis-2")
    assert "unique_orphan_name" in {r["name"] for r in facts["dead_code"]["shown"]}
    assert facts["markdown_outside_docs_total"] == 0


# ---------- 6. jscpd: скачивание только с разрешением, во временную папку вне проекта ----------

NODE_AND_NPM = {"node": "/bin/node", "npm": "/bin/npm"}.get


def test_jscpd_without_permission_asks_instead_of_downloading(tmp_path: Path) -> None:
    calls, runner = fake_runner()
    home = tmp_path / "home"
    results = tools.run_baseline(
        tmp_path, ["jscpd"], {"python": 1}, runner, NODE_AND_NPM, rel=["a.py"], jscpd_home=home
    )
    assert "нужно разрешение владельца (allow_download)" in results[0]["status"] and not calls
    assert not home.exists()
    nothing = tools.run_baseline(
        tmp_path, ["jscpd"], {"python": 1}, runner, nowhere, rel=["a.py"], jscpd_home=home
    )
    assert nothing[0]["status"].startswith("не установлен")


def test_jscpd_with_permission_installs_outside_the_project_and_writes_only_to_the_report(
    tmp_path: Path,
) -> None:
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    calls: list[list[str]] = []

    def runner(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
        calls.append(argv)
        if argv[1] == "install":  # npm: «скачал» пакет
            write(home / "node_modules" / "jscpd" / "run-jscpd.js", "// launcher\n")
            assert cwd == home  # установка идёт не в проекте
        return 0, "", ""

    report_dir = project / "parch-analysis-2"
    tools.run_baseline(
        project, ["jscpd"], {"python": 2, "csharp": 1}, runner, NODE_AND_NPM,
        rel=["a.py"], protected=("data",), report_dir=report_dir, allow_download=True, jscpd_home=home,
    )  # fmt: skip
    install, scan = calls
    assert install[:2] == ["/bin/npm", "install"] and f"jscpd@{tools.JSCPD_VERSION}" in install
    assert scan[:2] == ["/bin/node", str(home / "node_modules" / "jscpd" / "run-jscpd.js")]
    assert scan[scan.index("--output") + 1] == str(report_dir / "baseline" / "jscpd")
    ignore = scan[scan.index("--ignore") + 1]
    assert "data/**" in ignore and "**/*.g.cs" in ignore
    assert scan[scan.index("--format") + 1] == "python,csharp"
    again = tools.run_baseline(
        project,
        ["jscpd"],
        {"python": 1},
        runner,
        NODE_AND_NPM,
        rel=["a.py"],
        report_dir=report_dir,
        jscpd_home=home,
    )
    assert (
        again[0]["status"] == "выполнен" and len(calls) == 3
    )  # второй раз без разрешения и без повторной установки


# ---------- 7. ложные кандидаты в мёртвый код и горячие точки ----------


def test_framework_handlers_attribute_methods_and_generated_files_are_not_dead_code(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "srv.py",
        "class H:\n    def do_GET(self) -> None: ...\n\n    def log_message(self, *a: object) -> None: ...\n\n    def really_orphan(self) -> None: ...\n",
    )
    write(
        tmp_path / "Plug.cs",
        "public class Plug\n{\n    public void Awake() { }\n    [HarmonyPostfix]\n    public static void Hook() { }\n    public void Postfix() { }\n    public void Lonely() { }\n}\n",
    )
    write(
        tmp_path / "Gen" / "PropertyCatalog.g.cs",
        "public class Catalog { public void Gen() { } }\n",
    )
    found = deadcode.candidates(tmp_path, ["srv.py", "Plug.cs", "Gen/PropertyCatalog.g.cs"])
    names = {r["name"] for r in found["shown"]}
    assert {"really_orphan", "Lonely"} <= names
    assert not names & {"do_GET", "log_message", "Awake", "Hook", "Postfix", "Gen", "Catalog"}


def test_generated_files_are_not_hotspots(tmp_path: Path) -> None:
    big = "int x;\n" * 50
    git(tmp_path, "init", "-q")
    for n in range(3):
        write(tmp_path / "PropertyCatalog.g.cs", big + f"// {n}\n")
        write(tmp_path / "Real.cs", big + f"// {n}\n")
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-q", "-m", f"c{n}")
    names = [h["path"] for h in history.hotspots(tmp_path, ["PropertyCatalog.g.cs", "Real.cs"])]
    assert names == ["Real.cs"]


def test_questions_are_one_simple_question_per_file_in_groups() -> None:
    dead = {
        "total": 3,
        "shown": [
            {"name": "a_one", "kind": "функция", "path": "ops/x.ps1", "line": 1},
            {"name": "a_two", "kind": "функция", "path": "ops/x.ps1", "line": 5},
            {"name": "Lonely", "kind": "метод", "path": "P.cs", "line": 9},
        ],
    }
    text = deadcode.questions_markdown([], dead)
    assert text.count("| ops/x.ps1 |") == 1 and "`a_one`, `a_two`" in text
    assert "по расписанию" in text and "игре или плагину" in text
    assert "рефлексия" not in text.lower()


# ---------- 8. ruff меряет по правилам стандарта ----------


def test_ruff_runs_with_the_standard_rules_and_skips_protected_zones(tmp_path: Path) -> None:
    calls, runner = fake_runner({"ruff": (1, RUFF_OUTPUT)})
    results = tools.run_baseline(
        tmp_path,
        ["ruff"],
        {"python": 1},
        runner,
        everything,
        rel=["a.py"],
        protected=("data", "saves"),
    )
    argv = calls[0]
    for part in ("--isolated", "E,F,I,B,UP", "--line-length", "100", "--no-cache"):
        assert part in argv, part
    excluded = [argv[i + 1] for i, a in enumerate(argv) if a == "--extend-exclude"]
    assert {"data", "saves"} <= set(excluded)
    assert results[0]["metrics"]["auto_fixable"] == 1758


def test_dotnet_runs_with_english_messages() -> None:
    import os

    captured: dict[str, str] = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    original = tools.subprocess.run
    try:
        tools.subprocess.run = lambda *a, **kw: captured.update(kw["env"]) or Done()  # type: ignore[assignment]
        tools.subprocess_runner(["x"], Path(os.getcwd()), 5)
    finally:
        tools.subprocess.run = original
    assert captured["DOTNET_CLI_UI_LANGUAGE"] == "en" and captured["PYTHONDONTWRITEBYTECODE"] == "1"


# ---------- карточка и документы ----------


def test_test_files_are_counted_per_language_not_for_the_whole_project(tmp_path: Path) -> None:
    write(tmp_path / "tests" / "test_a.py", "def test_a() -> None: ...\n")
    write(tmp_path / "tests" / "test_b.py", "def test_b() -> None: ...\n")
    write(tmp_path / "plugin" / "ToolTests.cs", "class ToolTests { }\n")
    write(tmp_path / "ops" / "run.ps1", "Write-Output 1\n")
    facts = analyze.inventory(tmp_path, [])
    assert facts["test_files_by_language"] == {"python": 2, "csharp": 1}
    values = {
        (r["language"], r["signal"]): r["value"]
        for r in facts["health_card"]
        if r["signal"] == "Число тестов и покрытие"
    }
    assert values[("python", "Число тестов и покрытие")].startswith(
        "файлов тестов на этом языке: 2"
    )
    assert values[("powershell", "Число тестов и покрытие")].startswith(
        "файлов тестов на этом языке: 0"
    )


def test_tests_the_analysis_did_not_run_are_shown_as_not_run_not_as_a_failure() -> None:
    tests: dict[str, Any] = {
        "tool": "tests",
        "status": "выполнен",
        "metrics": {"files_run": 27, "tests_passed": 420, "tests_failed": 0, "files_not_run": 36},
    }
    rows = report.health_card(
        {"python": 5}, {}, [], {"total": 0, "shown": []}, [tests], {"python": 77}
    )
    values = {r["signal"]: r["value"] for r in rows}
    assert (
        "запускались анализом: 27 файлов, 420 тестов, упало 0" in values["Число тестов и покрытие"]
    )
    assert "не запускались анализом" in values["Число тестов и покрытие"]
    assert "это не провал" in values["Число тестов и покрытие"]
    assert values["Сборка и тесты проходят"] == "проверено запуском тестов: все прошли"


def test_the_skill_and_the_journals_carry_the_new_rules() -> None:
    root = Path(__file__).resolve().parent.parent
    skill = " ".join(
        (root / "plugin" / "skills" / "analyze-existing" / "SKILL.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for fact in (
        "report_dir",
        "allow_download",
        "allow_build_steps",
        "не запускались анализом",
        "`metrics`",
        "ответ А/Б/В",
    ):
        assert fact in skill, fact
    lessons = (root / "docs" / "LESSONS.md").read_text(encoding="utf-8")
    assert "проходит живой прогон на пилоте до слияния" in lessons
    backlog = (root / "docs" / "BACKLOG.md").read_text(encoding="utf-8")
    assert backlog.count("Исправлено в F15 PR 1b") == 8 and "по факту запуска процесса" in backlog
