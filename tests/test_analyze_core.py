# ruff: noqa: E501
"""F15, PR 1: ядро анализа: равноценные файлы, снимок инструментов, горячие точки, мёртвый код, риски, план приведения.

Спецификация: docs/specs/F15-analyze-existing.md; исходный документ: docs/design/analyze-existing.md.
На каждое правило есть заведомо плохой пример, который проверка обязана остановить.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from test_analyze_existing import call, git, write

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "plugin" / "skills" / "analyze-existing" / "scripts"


def load(name: str) -> Any:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analyze: Any = load("analyze")
equivalents: Any = load("analyze_equivalents")
plan: Any = load("analyze_plan")
history: Any = load("analyze_history")
tools: Any = load("analyze_tools")
deadcode: Any = load("analyze_deadcode")
report: Any = load("analyze_report")

BIG = "".join(f"def f{i}() -> int:\n    return {i}\n\n" for i in range(40))


@pytest.fixture
def owner_project(tmp_path: Path) -> Path:
    """Проект со своим устройством: равноценные файлы вместо файлов стандарта, три языка, история git."""
    write(tmp_path / "master_plan.md", "# План\n\nЗачем: оценщик решений.\n")
    write(
        tmp_path / "BACKLOG.md",
        "# Бэклог\n\n**Готово, когда** тест проходит.\n\n**Готово, когда** отчёт есть.\n",
    )
    write(
        tmp_path / "DECISIONS.md",
        "# Решения\n\n- 2026-09-01 — решение A — почему A — шаг 1\n- 2026-09-02 — решение B — почему B — шаг 2\n",
    )
    write(tmp_path / "STATUS.md", "# STATUS\n")
    write(tmp_path / "docs" / "incidents" / "2026-09-02_crash.md", "# сбой\n")
    write(tmp_path / "CLAUDE.md", "Правила проекта.\n")
    write(
        tmp_path / "scripts" / "app.py",
        BIG
        + "def used_func() -> int:\n    return 1\n\n\ndef orphan_func() -> int:\n    return 2\n",
    )
    write(tmp_path / "scripts" / "untested.py", BIG)
    write(
        tmp_path / "tests" / "test_app.py",
        "from scripts.app import used_func\n\n\ndef test_it() -> None:\n    assert used_func()\n",
    )
    write(
        tmp_path / "plugin" / "Tool.cs",
        "public class Tool\n{\n    public void OrphanMethod() { }\n    public void UsedMethod() { }\n}\n",
    )
    write(
        tmp_path / "plugin" / "Main.cs", "class Main2 { void Go() { new Tool().UsedMethod(); } }\n"
    )
    write(
        tmp_path / "ops" / "run.ps1",
        "function Orphan-Thing { }\nfunction Use-Thing { }\nUse-Thing\n",
    )
    git(tmp_path, "init", "-q")
    for number in range(5):  # app.py и untested.py меняются часто
        write(
            tmp_path / "scripts" / "app.py",
            BIG
            + f"# правка {number}\ndef used_func() -> int:\n    return 1\n\n\ndef orphan_func() -> int:\n    return 2\n",
        )
        write(tmp_path / "scripts" / "untested.py", BIG + f"# правка {number}\n")
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-q", "-m", f"c{number}")
    return tmp_path


def facts(project: Path, zones: list[str] | None = None) -> dict[str, Any]:
    return analyze.inventory(project, zones or [])


# ---------- равноценные файлы ----------


def test_equivalent_files_are_found_and_counted_without_penalty(owner_project: Path) -> None:
    found = {e["role"]: e for e in equivalents.find_equivalents(owner_project)}
    assert found["goal"]["path"] == "master_plan.md" and found["goal"]["standard"] == "docs/GOAL.md"
    assert found["decisions"]["path"] == "DECISIONS.md" and not found["decisions"]["is_standard"]
    assert (
        found["incidents"]["path"] == "docs/incidents/" and found["status"]["path"] == "STATUS.md"
    )
    assert "lessons" not in found and "modules" not in found  # чего нет, того нет
    card = {r["id"]: r for r in facts(owner_project)["p_card"]}
    assert (
        card["P1"]["status"] == "частично"
        and "равноценен GOAL.md, засчитывается" in card["P1"]["why"]
    )
    assert (
        card["P6"]["status"] == "частично" and "равноценен ADR, засчитывается" in card["P6"]["why"]
    )
    assert card["P2"]["status"] == "частично" and "равноценные файлы засчитаны" in card["P2"]["why"]
    assert card["P10"]["why"].startswith("отчётов об инцидентах: 1")


def test_a_project_without_those_files_gets_absent_not_the_equivalent_credit(
    tmp_path: Path,
) -> None:
    write(tmp_path / "src" / "a.py", "x = 1\n")
    card = {r["id"]: r["status"] for r in facts(tmp_path)["p_card"]}
    assert card["P1"] == card["P6"] == card["P2"] == "отсутствует"


def test_standard_named_files_are_marked_as_standard(tmp_path: Path) -> None:
    write(tmp_path / "docs" / "GOAL.md", "# GOAL\n\n- **G1.** Критерий\n")
    row = equivalents.find_equivalents(tmp_path)[0]
    assert row["is_standard"] is True and row["path"] == "docs/GOAL.md"


# ---------- план приведения ----------


def test_the_plan_proposes_renames_to_standard_names_safe_first(owner_project: Path) -> None:
    data = facts(owner_project)
    rows = data["plan"]
    routes = {(r["action"], r["old"], r["new"]) for r in rows}
    assert ("Переименовать", "DECISIONS.md", "docs/adr/") in routes
    assert ("Переименовать", "master_plan.md", "docs/GOAL.md") in routes
    assert ("Переименовать", "docs/incidents/", "state/incidents/") in routes
    assert ("Перестроить", "BACKLOG.md", "state/features.json") in routes
    assert all(r["method"] == "git mv" for r in rows if r["action"] == "Переименовать")
    assert data["plan_problems"] == []


def test_a_protected_zone_needs_a_separate_yes(owner_project: Path) -> None:
    rows = facts(owner_project, ["docs/incidents"])["plan"]
    incident = next(r for r in rows if r["old"] == "docs/incidents/")
    assert incident["needs_owner_yes"] is True and "отдельное «да»" in incident["risk"]
    plain = next(r for r in rows if r["old"] == "master_plan.md")
    assert plain["needs_owner_yes"] is False
    assert rows.index(plain) < rows.index(incident)  # безопасное раньше рискованного


def test_code_without_tests_is_risky_unless_a_test_exists() -> None:
    eq: list[dict[str, Any]] = [
        {
            "role": "modules",
            "title": "карта",
            "path": "tools/helper.py",
            "standard": "src/helper.py",
            "is_standard": False,
            "action": "Переименовать",
            "note": "x",
        }
    ]
    risky = plan.build_plan(eq, ["tools/helper.py"], [])[0]
    assert risky["risk"] == plan.RISKY and "характеризационный тест" in risky["precondition"]
    assert risky["code_without_tests"] is True
    safe = plan.build_plan(eq, ["tools/helper.py", "tests/test_helper.py"], [])[0]
    assert safe["risk"] == "безопасно" and not safe["precondition"]


def test_deletion_rows_exist_only_for_confirmed_candidates_with_proof_and_a_tag() -> None:
    assert not [r for r in plan.build_plan([], [], []) if r["action"] == "Удалить"]
    confirmed = [{"name": "orphan_func", "path": "scripts/app.py", "kind": "функция"}]
    delete = plan.build_plan([], ["scripts/app.py"], [], confirmed, "2026-10-05")[0]
    assert delete["action"] == "Удалить" and "1 вхождение" in delete["proof"]
    assert (
        delete["archive_tag"] == "archive/orphan_func-2026-10-05"
        and "поимённое" in delete["confirmation"]
    )
    assert delete["needs_owner_yes"] is True
    assert plan.validate([delete], []) == []


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "action": "Удалить",
                "old": "a.py",
                "new": "",
                "proof": "",
                "archive_tag": "archive/a",
                "confirmation": "поимённое утверждение владельца",
            },
            "без доказательства",
        ),
        (
            {
                "action": "Удалить",
                "old": "a.py",
                "new": "",
                "proof": "поиск",
                "archive_tag": "",
                "confirmation": "поимённое утверждение владельца",
            },
            "без архивной метки",
        ),
        (
            {
                "action": "Удалить",
                "old": "a.py",
                "new": "",
                "proof": "поиск",
                "archive_tag": "archive/a",
                "confirmation": "",
            },
            "без поимённого утверждения",
        ),
        (
            {
                "action": "Перенести",
                "old": "a/",
                "new": "b/",
                "method": "mv",
                "verify": "прогнать тесты",
            },
            "через git mv",
        ),
        (
            {
                "action": "Перенести",
                "old": "a/",
                "new": "b/",
                "method": "git mv",
                "verify": "ничего",
            },
            "без обновления всех ссылок и зелёных тестов",
        ),
        (
            {
                "action": "Переименовать",
                "old": "data/x.csv",
                "new": "data/y.csv",
                "method": "git mv",
                "verify": plan.LINKS,
                "needs_owner_yes": False,
            },
            "неприкосновенная зона",
        ),
        (
            {
                "action": "Перенести",
                "old": "a.py",
                "new": "b.py",
                "method": "git mv",
                "verify": plan.LINKS,
                "code_without_tests": True,
                "risk": "безопасно",
                "precondition": "",
            },
            "характеризационного теста",
        ),
        ({"action": "Сжечь", "old": "a", "new": ""}, "неизвестное действие"),
    ],
)
def test_plan_rows_that_break_a_safeguard_are_rejected(row: dict[str, Any], expected: str) -> None:
    problems = plan.validate([row], ["data"])
    assert any(expected in p for p in problems), problems


def test_the_plan_markdown_lists_actions_and_safeguards(owner_project: Path) -> None:
    text = plan.plan_markdown(facts(owner_project, ["data/"])["plan"], ["data"])
    for fact in (
        "| Действие |",
        "Переименовать",
        "git mv",
        "поимённым утверждением",
        "(data)",
        "F19",
        "характеризационного теста",
    ):
        assert fact in text, fact


# ---------- горячие точки ----------


def test_hotspots_rank_big_often_changed_code_and_flag_missing_tests(owner_project: Path) -> None:
    rel = sorted(
        p.relative_to(owner_project).as_posix()
        for p in owner_project.rglob("*.*")
        if ".git" not in p.parts
    )
    rows = history.hotspots(owner_project, rel)
    by_path = {r["path"]: r for r in rows}
    assert by_path["scripts/app.py"]["has_test"] is True  # tests/test_app.py
    assert by_path["scripts/untested.py"]["has_test"] is False
    assert by_path["scripts/untested.py"]["changes"] == 5
    assert all("tests/" not in p for p in by_path)  # сам тест не горячая точка
    assert rows == sorted(rows, key=lambda r: (-r["score"], r["path"]))


def test_hotspots_outside_git_are_empty(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x = 1\n")
    assert history.hotspots(tmp_path, ["a.py"]) == []


# ---------- инструменты по языкам ----------


def test_tools_are_detected_from_project_files_without_running_anything(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        "[tool.ruff]\nline-length = 100\n[tool.pyright]\nstrict = ['x']\n[tool.pytest.ini_options]\n",
    )
    write(tmp_path / "src" / "a.py", "x = 1\n")
    write(
        tmp_path / "app" / "App.csproj",
        "<Project><PropertyGroup><Nullable>enable</Nullable></PropertyGroup></Project>\n",
    )
    write(tmp_path / "app" / "App.cs", "class App { }\n")
    found = tools.detect(
        tmp_path,
        ["pyproject.toml", "src/a.py", "app/App.csproj", "app/App.cs"],
        {"python": 1, "csharp": 1, "typescript": 0, "powershell": 0},
    )
    assert set(found) == {"python", "csharp"}  # языков нет, строк нет
    python = {t["task"]: t["configured"] for t in found["python"]}
    assert (
        python["Проверка типов"]
        and python["Линтер и форматирование"]
        and python["Тесты и покрытие"]
    )
    assert not python["Правила архитектуры"] and not python["Дублирование"]
    csharp = {t["task"]: t["configured"] for t in found["csharp"]}
    assert csharp["Проверка типов"] and not csharp["Правила архитектуры"]


def test_every_language_of_the_design_table_has_the_six_tasks() -> None:
    for language, rows in tools.TABLE.items():
        assert len(rows) == 6, language
        assert [t.task for t in rows][0] == "Проверка типов"


# ---------- снимок: запуск только с разрешения, только отчёт ----------


def test_baseline_runs_only_what_was_asked_and_never_an_unknown_tool(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
        calls.append(argv)
        return 1, "a.py:1:1: E501 длинная строка\n", ""

    langs = {"python": 3, "csharp": 0, "typescript": 0, "powershell": 0}
    which = {"ruff": "/bin/ruff"}.get
    results = tools.run_baseline(
        tmp_path, ["ruff", "pyright", "dotnet-build"], langs, runner, which
    )
    by_tool = {r["tool"]: r for r in results}
    assert by_tool["ruff"]["status"] == "выполнен" and by_tool["ruff"]["returncode"] == 1
    assert by_tool["pyright"]["status"].startswith("не установлен")
    assert by_tool["dotnet-build"]["status"].startswith("пропущен")
    assert len(calls) == 1 and calls[0][:2] == ["/bin/ruff", "check"] and "--no-cache" in calls[0]
    with pytest.raises(ValueError, match="неизвестный инструмент"):
        tools.run_baseline(tmp_path, ["rm"], langs, runner, which)
    assert tools.run_baseline(tmp_path, [], langs, runner, which) == []


def test_no_runnable_tool_can_change_project_files() -> None:
    for tool in tools.RUNNABLE:
        text = " ".join(tool.argv)
        assert "--fix" not in text and "--write" not in text and " -i" not in f" {text}"
    ruff = next(t for t in tools.RUNNABLE if t.key == "ruff")
    assert "--no-cache" in ruff.argv


def test_baseline_results_are_written_only_inside_the_report_folder(tmp_path: Path) -> None:
    folder = tmp_path / "parch-analysis"
    written = tools.write_baseline(
        folder, [{"tool": "ruff", "status": "выполнен", "returncode": 0}]
    )
    assert written == ["parch-analysis/baseline/ruff.json"]
    assert (
        json.loads((folder / "baseline" / "ruff.json").read_text(encoding="utf-8"))["tool"]
        == "ruff"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["parch-analysis"]


def test_the_baseline_command_without_a_run_list_starts_nothing(owner_project: Path) -> None:
    out = call({"command": "baseline", "project_dir": str(owner_project)})
    assert out["results"] == [] and out["written"] == []
    assert (owner_project / "parch-analysis" / ".parch-analysis").is_file()


# ---------- мёртвый код: вопросы, не удаление ----------


def test_dead_code_candidates_are_names_seen_only_in_their_own_declaration(
    owner_project: Path,
) -> None:
    rel = sorted(
        p.relative_to(owner_project).as_posix()
        for p in owner_project.rglob("*.*")
        if ".git" not in p.parts
    )
    found = deadcode.candidates(owner_project, rel)
    names = {r["name"] for r in found["shown"]}
    assert {"orphan_func", "OrphanMethod", "Orphan-Thing"} <= names
    assert not names & {
        "used_func",
        "UsedMethod",
        "Use-Thing",
    }  # их вызывают, в том числе из теста и скрипта
    assert not any(n.startswith(("test", "_")) for n in names)


def test_generated_decorated_and_test_code_are_not_candidates(tmp_path: Path) -> None:
    write(
        tmp_path / "a.py",
        "import x\n\n\n@x.route\ndef handler_view() -> None: ...\n\n\ndef _private_one() -> None: ...\n",
    )
    write(tmp_path / "tests" / "test_a.py", "def helper_in_tests() -> None: ...\n")
    write(tmp_path / "docs" / "Big.decompiled.cs", "public class DecompiledThing { }\n")
    rel = ["a.py", "tests/test_a.py", "docs/Big.decompiled.cs"]
    assert deadcode.candidates(tmp_path, rel)["total"] == 0


def test_questions_ask_the_three_questions_for_every_candidate() -> None:
    dead = {
        "total": 1,
        "shown": [{"name": "orphan_func", "kind": "функция", "path": "a.py", "line": 3}],
    }
    # PR 1b: три формальных вопроса заменены одним простым вопросом на файл (название теста оставлено ради храповика).
    text = deadcode.questions_markdown([{"id": "P4", "question": "Где хранится?"}], dead)
    for fact in (
        "| a.py | `orphan_func` |",
        "вы запускаете из командной строки или другой программой",
        "А / Б / В",
        "| P4 |",
    ):
        assert fact in text, fact
    assert "ничего не удаляет" in text
    assert "рефлексии" not in text and "keep-until" not in text


# ---------- риски и карточка здоровья ----------


def test_risks_are_five_to_ten_ordered_by_weight_and_tell_the_consequence(
    owner_project: Path,
) -> None:
    risks = facts(owner_project)["risks"]
    assert 5 <= len(risks) <= 10
    titles = [r["title"] for r in risks]
    assert titles[0] == "Правила держатся на дисциплине агента, а не на принуждении"
    assert any(t.startswith("Горячие точки без тестов") for t in titles)
    assert "Нет автоматических проверок (CI)" in titles
    assert all(r["consequence"] for r in risks)


def test_the_health_card_says_not_measured_until_tools_were_run(owner_project: Path) -> None:
    rows = facts(owner_project)["health_card"]
    python = {r["signal"]: r["value"] for r in rows if r["language"] == "python"}
    assert (
        python["Сборка и тесты проходят"] == "не измерялось"
        and python["Дублирование"] == "не измерялось"
    )
    assert python["Горячие точки"].startswith("горячих точек без теста:")
    assert python["Межъязыковые швы без тестов"] == "оценивается в PR 2 (карта и швы)"
    assert {r["language"] for r in rows} == {"python", "csharp", "powershell"}
    assert {r["alarming_if"] for r in rows if r["signal"] == "Дублирование"} == {
        "процент выше примерно 5–10%"
    }


def test_the_health_card_uses_baseline_results_when_they_exist() -> None:
    metrics = {"projects": 6, "built": 5, "failed": ["a.csproj"], "warnings": 7, "errors": 2}
    ran = [{"tool": "dotnet-build", "status": "выполнен", "returncode": 1, "metrics": metrics}]
    rows = report.health_card({"csharp": 2}, {"csharp": []}, [], {"total": 0, "shown": []}, ran, 4)
    assert {r["signal"]: r["value"] for r in rows}["Сборка и тесты проходят"] == (
        "сборка: собирается проектов 5 из 6, предупреждений 7, ошибок 2; не собрались: a.csproj"
    )


# ---------- команда write и проверка «ничего не менялось» ----------


def test_write_creates_the_analysis_files_inside_the_report_folder_only(
    owner_project: Path,
) -> None:
    call({"command": "inventory", "project_dir": str(owner_project)})
    out = call(
        {"command": "write", "project_dir": str(owner_project), "protected_paths": ["data/"]}
    )
    folder = owner_project / "parch-analysis"
    names = ("QUESTIONS.md", "PLAN.md", "drafts/GOAL.md", "drafts/ADR-DRAFTS.md", "facts.json")
    assert set(out["written"]) == {f"parch-analysis/{n}" for n in names}
    for name in ("QUESTIONS.md", "PLAN.md", "drafts/GOAL.md", "drafts/ADR-DRAFTS.md", "facts.json"):
        assert (folder / name).is_file(), name
    assert "черновик, ждёт утверждения владельца" in (folder / "drafts" / "GOAL.md").read_text(
        encoding="utf-8"
    )
    assert "master_plan.md" in (folder / "drafts" / "GOAL.md").read_text(encoding="utf-8")
    assert "записей с датой: 2" in (folder / "drafts" / "ADR-DRAFTS.md").read_text(encoding="utf-8")
    stored = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
    assert stored["protected_paths"] == ["data"] and stored["plan"] and stored["risks"]
    assert out["plan_problems"] == []
    assert call({"command": "verify", "project_dir": str(owner_project)})["ok"] is True
    again = call(
        {"command": "write", "project_dir": str(owner_project)}
    )  # свои файлы можно переписать
    assert again["written"]


def test_write_refuses_a_foreign_report_folder(owner_project: Path) -> None:
    write(owner_project / "parch-analysis" / "mine.md", "чужое\n")
    out = call({"command": "write", "project_dir": str(owner_project)}, expect=1)
    assert "создана не этим анализом" in out["error"]
    assert (owner_project / "parch-analysis" / "mine.md").read_text(encoding="utf-8") == "чужое\n"


def test_a_permitted_csharp_build_leaves_bin_and_obj_without_failing_verify(
    owner_project: Path,
) -> None:
    analyze.inventory(owner_project, [])
    saved = analyze.snapshot_file(owner_project)
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["extra_allowed"] = ["bin", "obj"]
    saved.write_text(json.dumps(data), encoding="utf-8")
    write(owner_project / "plugin" / "bin" / "Debug" / "Tool.dll", "x\n")
    write(owner_project / "plugin" / "obj" / "project.assets.json", "{}\n")
    assert analyze.verify(owner_project, None)["ok"] is True
    write(
        owner_project / "plugin" / "NewFile.cs", "class N { }\n"
    )  # посторонний файл всё равно нарушение
    bad = analyze.verify(owner_project, None)
    assert bad["ok"] is False and any("NewFile.cs" in line for line in bad["changed"])


def test_without_the_permission_a_build_directory_is_a_violation(owner_project: Path) -> None:
    analyze.inventory(owner_project, [])
    write(owner_project / "plugin" / "bin" / "Tool.dll", "x\n")
    assert analyze.verify(owner_project, None)["ok"] is False


# ---------- документ, спецификация, навык ----------


def test_the_design_document_is_in_the_repository_and_the_spec_points_to_it() -> None:
    design = (REPO / "docs" / "design" / "analyze-existing.md").read_text(encoding="utf-8")
    for heading in (
        "## Инструменты по языкам",
        "## Карточка здоровья проекта",
        "## Что анализ не делает и почему",
        "## Как проходит анализ",
    ):
        assert heading in design, heading
    spec = (REPO / "docs" / "specs" / "F15-analyze-existing.md").read_text(encoding="utf-8")
    assert (
        "docs/design/analyze-existing.md" in spec
        and "Самого документа в репозитории нет" not in spec
    )
    assert "Статус: утверждена владельцем, 2026-10-04" in spec
    assert "Разрешение на запуск инструментов при анализе fm26-data" in spec


def test_the_skill_follows_the_design_steps_and_the_permission_rules() -> None:
    skill = (REPO / "plugin" / "skills" / "analyze-existing" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    for fact in (
        '"command": "baseline"', '"command": "write"', "protected_paths", "docs/design/analyze-existing.md",
        "Проверка отчёта отдельным агентом", "Что анализ не делает", "Карточка здоровья по языкам",
        "если тесты могут запускать игру", "только с разрешения", "5-10",
    ):  # fmt: skip
        assert fact in skill, fact


def test_the_cli_inventory_returns_all_new_sections(owner_project: Path) -> None:
    out = call(
        {"command": "inventory", "project_dir": str(owner_project), "protected_paths": ["data/"]}
    )
    for key in (
        "equivalents",
        "tools",
        "hotspots",
        "dead_code",
        "risks",
        "plan",
        "plan_problems",
        "health_card",
        "protected_paths",
    ):
        assert key in out, key
    assert out["protected_paths"] == ["data"]


def test_modules_run_as_a_script_without_a_package(owner_project: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "analyze.py")],
        input=json.dumps({"command": "inventory", "project_dir": str(owner_project)}),
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
