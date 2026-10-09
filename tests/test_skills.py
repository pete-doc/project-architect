"""Навыки /parch:init-project, /parch:adr, /parch:doctor и шаблоны документов."""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import HOOKS, INIT_ANSWERS, HookResult, bash, file_call, run_hook

PLUGIN = HOOKS.parent
SKILLS = PLUGIN / "skills"
TEMPLATES = PLUGIN / "templates"
INIT = SKILLS / "init-project" / "scripts" / "init_project.py"
ADR = SKILLS / "adr" / "scripts" / "adr.py"
DOCTOR = SKILLS / "doctor" / "scripts" / "doctor.py"
REPO = PLUGIN.parent


def run_script(script: Path, request: dict[str, Any]) -> tuple[int, Any, str]:
    process = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=180,
    )
    out = process.stdout.decode("utf-8")
    return process.returncode, json.loads(out) if out.strip() else None, process.stderr.decode()


def init(project: Path, **extra: Any) -> dict[str, Any]:
    request = {
        "project_dir": str(project),
        "name": "Мой сервис",
        "languages": ["python"],
        "description": "Считает заказы.",
        "priorities": "надёжность\nпонятность",
        **INIT_ANSWERS,
        **extra,
    }
    code, result, error = run_script(INIT, request)
    assert code == 0, error
    return result


def sh_run(args: list[str], stdin: str, cwd: Path) -> HookResult:
    process = subprocess.run(
        ["sh", str(HOOKS / "run-hook.cmd"), *args],
        input=stdin.encode("utf-8"),
        capture_output=True,
        cwd=cwd,
        check=False,
        timeout=120,
    )
    return HookResult(process)


# ---------- шаблоны ----------

DOC_TEMPLATES = ["CONSTITUTION", "MODULES", "INTERFACES", "LESSONS", "QUESTIONS"]


@pytest.mark.parametrize("name", DOC_TEMPLATES)
def test_every_document_template_explains_itself(name: str) -> None:
    text = (TEMPLATES / "docs" / f"{name}.md").read_text(encoding="utf-8")
    assert "Как пользоваться" in text


def test_adr_template_and_state_templates_exist() -> None:
    adr_template = (TEMPLATES / "docs" / "adr" / "0000-template.md").read_text(encoding="utf-8")
    assert "- Статус: proposed" in adr_template
    assert json.loads((TEMPLATES / "state" / "features.json").read_text("utf-8"))["features"] == []
    assert "Как пользоваться" in (TEMPLATES / "state" / "STATUS.md").read_text(encoding="utf-8")
    assert (TEMPLATES / "ci" / "circleci" / "python.yml").is_file()  # шаблон CircleCI (ADR-0022)


SKILL_NAMES = ["init-project", "adr", "doctor"]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter(name: str) -> None:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "frontmatter должен быть первой строкой"
    front = text.split("\n---\n", 1)[0]
    assert f"name: {name}" in front
    assert re.search(r"description: .*[а-яА-Я]", front), "описание на русском"
    if name in {"init-project", "doctor"}:
        assert "disable-model-invocation: true" in front  # запускает только владелец
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" in text  # скрипты идут через launcher


# ---------- /parch:init-project ----------

EXPECTED_FILES = [
    "docs/CONSTITUTION.md",
    "docs/MODULES.md",
    "docs/INTERFACES.md",
    "docs/LESSONS.md",
    "docs/QUESTIONS.md",
    "docs/adr/0000-template.md",
    "docs/adr/README.md",
    "state/features.json",
    "state/STATUS.md",
    ".circleci/config.yml",
    ".claude/settings.json",
    ".gitignore",
    "pyproject.toml",
    "requirements-dev.txt",
    "tests/test_smoke.py",
    "requirements.txt",
    "state/baseline.json",
    ".github/parch/parch_ci.py",
]


def test_init_creates_a_working_structure(tmp_path: Path) -> None:
    result = init(tmp_path)
    for rel in EXPECTED_FILES:
        assert (tmp_path / rel).is_file(), rel
    assert result["created"][-1].startswith("docs/CONSTITUTION.md")  # включает защиту последним
    constitution = (tmp_path / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert "Мой сервис" in constitution
    assert (
        "- pip: pytest, pytest-cov, coverage, ruff, pyright, import-linter, vulture" in constitution
    )
    assert "- pytest -q" in constitution  # та же команда, что в CI (не `python -m pytest`)
    assert "python -m pytest" not in constitution
    assert "{{" not in constitution
    assert "- Python 3.12+" in constitution
    assert "надёжность" in constitution


def test_init_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    init(tmp_path)
    (tmp_path / "docs" / "LESSONS.md").write_text("мои уроки\n", encoding="utf-8")
    second = init(tmp_path)
    assert (tmp_path / "docs" / "LESSONS.md").read_text(encoding="utf-8") == "мои уроки\n"
    assert "docs/LESSONS.md" in second["skipped_existing"]
    assert "docs/CONSTITUTION.md" in second["skipped_existing"]


def test_init_keeps_existing_ci_and_merges_permissions(tmp_path: Path) -> None:
    (tmp_path / ".circleci").mkdir(parents=True)
    (tmp_path / ".circleci" / "config.yml").write_text("version: 2.1 # mine\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"model": "opus", "permissions": {"deny": ["Bash(curl *)"]}}), encoding="utf-8"
    )
    result = init(tmp_path)
    assert (tmp_path / ".circleci" / "config.yml").read_text() == "version: 2.1 # mine\n"
    assert ".circleci/config.yml" in result["skipped_existing"]
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "opus"
    assert "Bash(curl *)" in settings["permissions"]["deny"]
    assert "Read(.env)" in settings["permissions"]["deny"]
    assert "Edit(/.github/**)" in settings["permissions"]["ask"]
    assert (
        "Edit(/.circleci/**)" in settings["permissions"]["ask"]
    )  # CI проекта на CircleCI (ADR-0022)


def test_every_supported_language_has_a_ci_template() -> None:
    spec = importlib.util.spec_from_file_location("init_project_module", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    templates: dict[str, str] = module.CI_TEMPLATES
    languages: dict[str, Any] = module.LANGUAGES
    assert set(templates) == set(languages)
    for name in templates.values():
        assert (REPO / "plugin" / "templates" / "ci" / name).is_file(), name


@pytest.mark.parametrize(
    "bad",
    [{"languages": []}, {"languages": ["cobol"]}, {"name": "  "}],
)
def test_init_rejects_bad_answers(tmp_path: Path, bad: dict[str, Any]) -> None:
    request = {
        "project_dir": str(tmp_path),
        "name": "x",
        "languages": ["python"],
        **INIT_ANSWERS,
        **bad,
    }
    code, _, error = run_script(INIT, request)
    assert code == 1
    assert error.startswith("init-project:")
    assert not (tmp_path / "docs" / "CONSTITUTION.md").exists()


def test_generated_python_project_passes_its_own_ci(tmp_path: Path) -> None:
    """«CI в пустом проекте сразу зелёный»: те же команды, что в шаблоне .circleci/config.yml."""
    init(tmp_path)
    ci = (tmp_path / ".circleci" / "config.yml").read_text(encoding="utf-8")
    for command in ("ruff check .", "ruff format --check .", "pyright"):
        assert f"command: {command}" in ci
    assert "timeout 600 pytest -v --junitxml=test-report.xml" in ci  # тесты под пределом времени
    for argv in (
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
        ["pyright"],
        ["pytest", "-q"],
    ):
        process = subprocess.run(
            [sys.executable, "-m", *argv],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert process.returncode == 0, f"{argv}: {process.stdout}{process.stderr}"


def test_generated_ci_catches_a_violation(tmp_path: Path) -> None:
    init(tmp_path)
    (tmp_path / "tests" / "test_bad.py").write_text("import os\n", encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."], cwd=tmp_path, capture_output=True, check=False
    )
    assert process.returncode != 0


def test_initialized_project_is_protected_by_the_hooks(tmp_path: Path) -> None:
    init(tmp_path)
    assert run_hook("guard_paths.py", file_call("Write", tmp_path / "notes.md"), tmp_path).blocked
    tests_edit = file_call("Write", tmp_path / "tests" / "test_x.py")
    assert run_hook("guard_paths.py", tests_edit, tmp_path).blocked
    ci = file_call("Edit", tmp_path / ".circleci" / "config.yml")
    ask = run_hook("guard_paths.py", ci, tmp_path)
    assert json.loads(ask.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert run_hook("guard_packages.py", bash("pip install flask"), tmp_path).blocked
    assert run_hook("guard_packages.py", bash("pip install ruff"), tmp_path).code == 0
    constitution = tmp_path / "docs" / "CONSTITUTION.md"
    # проверки идут тем же интерпретатором, что и тесты: на Windows `python` из PATH бывает другим
    constitution.write_text(
        constitution.read_text(encoding="utf-8").replace("- python ", f'- "{sys.executable}" '),
        encoding="utf-8",
    )
    stop = run_hook("stop_gate.py", {"hook_event_name": "Stop"}, tmp_path)
    assert stop.code == 0, stop.stderr  # проверки из CONSTITUTION проходят в чистом проекте


# ---------- /parch:adr ----------


def adr_new(project: Path, title: str, kind: str) -> dict[str, Any]:
    request = {"command": "new", "project_dir": str(project), "title": title, "type": kind}
    code, result, error = run_script(ADR, request)
    assert code == 0, error
    return result


def test_adr_new_creates_a_numbered_file_and_updates_the_index(project: Path) -> None:
    result = adr_new(project, "Выбор базы данных", "irreversible")
    assert result["created"] == "docs/adr/0003-vybor-bazy-dannyh.md"
    assert result["status"] == "proposed"
    text = (project / result["created"]).read_text(encoding="utf-8")
    assert text.startswith("# ADR-0003. Выбор базы данных\n")
    assert "- Статус: proposed" in text
    assert "- Тип решения: необратимое" in text
    assert "## Объяснение для владельца (одна страница)" in text
    assert "<!--" not in text.split("## Контекст")[0].replace("<!-- ", "<!--")  # маркеры убраны
    index = (project / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    assert (
        "| [0003](0003-vybor-bazy-dannyh.md) | Выбор базы данных | proposed | необратимое |"
        in index
    )
    assert "| [0001](0001-accepted.md) | ADR-0001 | accepted |" in index or "0001" in index


def test_reversible_adr_has_no_owner_explanation_block(project: Path) -> None:
    result = adr_new(project, "Имя переменной", "reversible")
    text = (project / result["created"]).read_text(encoding="utf-8")
    assert "- Тип решения: обратимое" in text
    assert "Объяснение для владельца" not in text
    assert "irreversible" not in text


def test_adr_numbers_increase(project: Path) -> None:
    first = adr_new(project, "Первое", "reversible")["number"]
    second = adr_new(project, "Второе", "reversible")["number"]
    assert (first, second) == ("0003", "0004")


def test_adr_index_lists_statuses_and_is_stable(project: Path) -> None:
    code, result, error = run_script(ADR, {"command": "index", "project_dir": str(project)})
    assert code == 0, error
    index = project / result["index"]
    before = index.read_text(encoding="utf-8")
    assert "| 0001 |" not in before and "accepted" in before and "proposed" in before
    run_script(ADR, {"command": "index", "project_dir": str(project)})
    assert index.read_text(encoding="utf-8") == before


def test_adr_is_created_even_without_docs_adr_folder(tmp_path: Path) -> None:
    result = adr_new(tmp_path, "Первое решение проекта", "reversible")
    assert result["created"].startswith("docs/adr/0001-")
    assert (tmp_path / "docs" / "adr" / "README.md").is_file()


@pytest.mark.parametrize(
    "request_body",
    [
        {"command": "new", "title": "x", "type": "maybe"},
        {"command": "new", "title": "   ", "type": "reversible"},
        {"command": "delete"},
    ],
)
def test_adr_rejects_bad_requests(request_body: dict[str, Any], tmp_path: Path) -> None:
    code, _, error = run_script(ADR, {"project_dir": str(tmp_path), **request_body})
    assert code == 1
    assert error.startswith("adr:")


def test_new_adr_is_never_accepted(project: Path) -> None:
    result = adr_new(project, "Что-то важное", "irreversible")
    text = (project / result["created"]).read_text(encoding="utf-8")
    assert "Статус: accepted" not in text


def test_product_adr_index_is_up_to_date(tmp_path: Path) -> None:
    """Индекс решений самого продукта собирается тем же скриптом, что и в проектах."""
    shutil.copytree(REPO / "docs" / "adr", tmp_path / "docs" / "adr")
    code, _, error = run_script(ADR, {"command": "index", "project_dir": str(tmp_path)})
    assert code == 0, error
    fresh = (tmp_path / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    committed = (REPO / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
    assert committed == fresh, "запусти /parch:adr index и закоммить docs/adr/README.md"
    # Шаблон ADR (0000-template.md) в индекс не входит, все настоящие решения обязаны входить.
    for adr_file in sorted((REPO / "docs" / "adr").glob("0*.md")):
        if adr_file.name == "0000-template.md":
            continue
        assert adr_file.name in committed
    assert "0000-template.md" not in committed


def test_adr_index_skips_only_the_template_and_lists_every_real_decision(tmp_path: Path) -> None:
    """Исключён именно шаблон: настоящий ADR без записи в индексе обязан попасть в индекс."""
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    shutil.copy(REPO / "docs" / "adr" / "0000-template.md", adr_dir / "0000-template.md")
    template = (adr_dir / "0000-template.md").read_text(encoding="utf-8")
    (adr_dir / "0042-real-decision.md").write_text(
        template.replace("0000", "0042"), encoding="utf-8"
    )
    code, _, error = run_script(ADR, {"command": "index", "project_dir": str(tmp_path)})
    assert code == 0, error
    index = (adr_dir / "README.md").read_text(encoding="utf-8")
    assert "0042-real-decision.md" in index
    assert "0000-template.md" not in index


# ---------- /parch:doctor ----------


def prepare() -> dict[str, Any]:
    code, result, error = run_script(DOCTOR, {"command": "prepare"})
    assert code == 0, error
    return result


def test_doctor_prepares_a_temporary_managed_project() -> None:
    result = prepare()
    project = Path(result["project"])
    try:
        assert project.name.startswith("parch-doctor-")
        assert (project / "docs" / "CONSTITUTION.md").is_file()
        assert (project / "victim").is_dir()
        assert [probe["id"] for probe in result["probes"]] == ["destructive", "markdown", "package"]
        assert "\\" not in result["project"]  # пути с прямыми слэшами годятся и для Git Bash
    finally:
        shutil.rmtree(project, ignore_errors=True)


def test_every_doctor_probe_is_stopped_by_its_hook(tmp_path: Path) -> None:
    """Пробы doctor вызывают hooks так, как это делает Claude Code в сессии вне проекта."""
    result = prepare()
    project = Path(result["project"])
    session = tmp_path / "session"  # сессия запущена в другой, не подключённой папке
    session.mkdir()
    try:
        by_id = {probe["id"]: probe for probe in result["probes"]}
        destructive = run_hook(
            "guard_destructive.py", bash(by_id["destructive"]["command"]), session, cwd=str(project)
        )
        assert destructive.blocked and by_id["destructive"]["expect_text"] in destructive.stderr
        write = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": by_id["markdown"]["file_path"]},
        }
        md = run_hook("guard_paths.py", write, session, cwd=str(project))
        assert md.blocked and by_id["markdown"]["expect_text"] in md.stderr
        package = run_hook(
            "guard_packages.py", bash(by_id["package"]["command"]), session, cwd=str(project)
        )
        assert package.blocked and by_id["package"]["expect_text"] in package.stderr
        code, checked, _ = run_script(DOCTOR, {"command": "check", "project": str(project)})
        assert code == 0
        assert checked["destructive_probe_was_blocked"] and checked["markdown_probe_was_blocked"]
    finally:
        shutil.rmtree(project, ignore_errors=True)


def test_doctor_detects_that_protection_did_not_work() -> None:
    result = prepare()
    project = Path(result["project"])
    try:
        shutil.rmtree(project / "victim")  # как если бы hook не сработал и rm -rf прошёл
        (project / "notes.md").write_text("x", encoding="utf-8")
        _, checked, _ = run_script(DOCTOR, {"command": "check", "project": str(project)})
        assert not checked["destructive_probe_was_blocked"]
        assert not checked["markdown_probe_was_blocked"]
    finally:
        shutil.rmtree(project, ignore_errors=True)


def test_doctor_cleanup_removes_only_its_own_temporary_projects(tmp_path: Path) -> None:
    result = prepare()
    code, done, _ = run_script(DOCTOR, {"command": "cleanup", "project": result["project"]})
    assert code == 0 and done["removed"]
    assert not Path(result["project"]).exists()
    precious = tmp_path / "my-project"
    precious.mkdir()
    code, _, error = run_script(DOCTOR, {"command": "cleanup", "project": str(precious)})
    assert code == 1 and "отказываюсь" in error
    assert precious.is_dir()


def test_doctor_diagnose_reports_the_facts_the_skill_needs() -> None:
    code, facts, error = run_script(DOCTOR, {"command": "diagnose"})
    assert code == 0, error
    for key in ("system", "python_candidates", "sh_found", "launcher_exists", "hook_scripts"):
        assert key in facts
    assert facts["launcher_exists"]
    assert all(hook["ok"] for hook in facts["hook_scripts"]), facts["hook_scripts"]
    assert [c["candidate"] for c in facts["python_candidates"]] == ["python3", "python", "py -3"]


# ---------- скрипты навыков запускаются через launcher ----------


@pytest.mark.skipif(shutil.which("sh") is None, reason="нужен sh")
def test_skill_scripts_run_through_the_launcher(tmp_path: Path) -> None:
    result = sh_run(
        ["../skills/adr/scripts/adr.py"],
        json.dumps(
            {"command": "new", "project_dir": str(tmp_path), "title": "Тест", "type": "reversible"}
        ),
        tmp_path,
    )
    assert result.code == 0, result.stderr
    assert json.loads(result.stdout)["created"].startswith("docs/adr/0001-")
    facts = sh_run(["../skills/doctor/scripts/doctor.py"], '{"command": "diagnose"}', tmp_path)
    assert facts.code == 0, facts.stderr
    assert json.loads(facts.stdout)["launcher_exists"]


def test_skills_work_from_an_installed_copy_of_the_plugin_only(tmp_path: Path) -> None:
    """При установке Claude Code видит только plugin/: шаблоны не ищутся рядом с этой папкой."""
    installed = tmp_path / "installed" / "plugin"
    shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__"))
    project = tmp_path / "project"
    request = {
        "project_dir": str(project),
        "name": "Копия",
        "languages": ["python"],
        **INIT_ANSWERS,
    }
    script = installed / "skills" / "init-project" / "scripts" / "init_project.py"
    code, result, error = run_script(script, request)
    assert code == 0, error
    assert (project / "docs" / "CONSTITUTION.md").is_file()
    assert (project / ".circleci" / "config.yml").is_file()
    assert not (tmp_path / "installed" / "templates").exists()
    assert result["created"]


# ---------- CI Python в сгенерированном проекте (фаза D) ----------

CI_SCRIPT = PLUGIN / "templates" / "ci" / "parch" / "parch_ci.py"


def parch_check(project: Path, check: str) -> subprocess.CompletedProcess[str]:
    script = project / ".github" / "parch" / "parch_ci.py"
    return subprocess.run(
        [sys.executable, str(script), check],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


def test_generated_project_passes_the_parch_checks_and_ships_the_script(tmp_path: Path) -> None:
    init(tmp_path)
    script = tmp_path / ".github" / "parch" / "parch_ci.py"
    assert script.read_bytes() == CI_SCRIPT.read_bytes().replace(b"\r\n", b"\n")
    checks = ("settings", "tests", "skips", "suppressions", "modules", "deps")
    for check in (*checks, "dead-code", "architecture", "coverage"):
        done = parch_check(tmp_path, check)
        assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
    workflow = (tmp_path / ".circleci" / "config.yml").read_text(encoding="utf-8")
    for check in (*checks, "architecture", "dead-code", "duplicates", "coverage"):
        assert f"parch_ci.py {check}" in workflow


def test_package_added_through_a_manifest_is_caught_in_the_generated_project(
    tmp_path: Path,
) -> None:
    """Закрывает дыру из ADR-0004: пакет в requirements.txt в обход команды установки."""
    init(tmp_path)
    with (tmp_path / "requirements.txt").open("a", encoding="utf-8") as handle:
        handle.write("flask==3.1.0\n")
    done = parch_check(tmp_path, "deps")
    assert done.returncode == 1
    assert "flask" in done.stdout and "Разрешённые пакеты" in done.stdout


def test_init_on_a_project_with_code_and_an_old_baseline_marks_it_existing(tmp_path: Path) -> None:
    """Плохой пример (самоприменение, F25 шаг 0в): baseline уже есть, код есть, а признака
    `existing_project` нет: правила состава проекта падали бы сразу, а не предупреждали."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "state").mkdir()
    old: dict[str, Any] = {"version": 1, "tests": {"python": []}, "skips": {}, "config": {}}
    (tmp_path / "state" / "baseline.json").write_text(json.dumps(old), encoding="utf-8")
    result = init(tmp_path)
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["existing_project"] is True
    assert baseline["tests"] == {"python": []}  # остальное содержимое не тронуто
    assert any("existing_project" in note for note in result["notes"])


def test_init_keeps_an_explicit_existing_project_false_and_writes_lf(tmp_path: Path) -> None:
    """Явная запись владельца не переписывается, а записанный файл без CRLF (целевая ОС Windows)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "state").mkdir()
    path = tmp_path / "state" / "baseline.json"
    old: dict[str, Any] = {"version": 1, "existing_project": False, "tests": {"python": []}}
    path.write_text(json.dumps(old), encoding="utf-8")
    result = init(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["existing_project"] is False
    assert any("existing_project: false" in note for note in result["notes"])
    (tmp_path / "second").mkdir()
    (tmp_path / "second" / "src").mkdir()
    (tmp_path / "second" / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "second" / "state").mkdir()
    fresh = tmp_path / "second" / "state" / "baseline.json"
    fresh.write_text(json.dumps({"version": 1, "tests": {"python": []}}), encoding="utf-8")
    init(tmp_path / "second")
    assert b"\r\n" not in fresh.read_bytes()


def test_init_on_a_project_without_code_leaves_an_old_baseline_alone(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    old: dict[str, Any] = {"version": 1, "tests": {"python": []}, "skips": {}, "config": {}}
    (tmp_path / "state" / "baseline.json").write_text(json.dumps(old), encoding="utf-8")
    init(tmp_path)
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert "existing_project" not in baseline  # кода нет: проект новый


def test_init_does_not_add_a_smoke_test_when_tests_already_exist(tmp_path: Path) -> None:
    """Плохой пример: у продукта 2000 тестов, а init добавлял `tests/test_smoke.py`, который
    основная сессия после включения охраны удалить не может (tests/ ей закрыт)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_app() -> None:\n    pass\n", "utf-8")
    result = init(tmp_path)
    assert not (tmp_path / "tests" / "test_smoke.py").exists()
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["tests"]["python"] == []  # известные тесты запишет владелец после прогона
    assert any("test_smoke.py не создан" in note for note in result["notes"])


def test_init_still_creates_the_smoke_test_for_a_project_without_tests(tmp_path: Path) -> None:
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "test_vendored.py").write_text("", encoding="utf-8")  # не свои
    init(tmp_path)
    assert (tmp_path / "tests" / "test_smoke.py").is_file()


def new_package(project: Path) -> Path:
    """Единственный пакет в src/ (имя транслитерации не фиксируется)."""
    found = [p for p in (project / "src").iterdir() if (p / "__init__.py").is_file()]
    assert len(found) == 1, found
    assert [p.name for p in (project / "src").iterdir()] == [found[0].name]
    return found[0]


def test_init_of_a_new_project_creates_a_package_with_a_docstring(tmp_path: Path) -> None:
    init(tmp_path)
    package = new_package(tmp_path)
    assert re.fullmatch(r"[a-z][a-z0-9_]*", package.name), package.name
    text = (package / "__init__.py").read_text(encoding="utf-8")
    assert text.lstrip().startswith(('"""', "'''")), text  # однострочное описание, а не пусто


def test_init_writes_one_real_forbidden_contract_against_pytest(tmp_path: Path) -> None:
    init(tmp_path)
    package = new_package(tmp_path)
    text = (tmp_path / ".importlinter").read_text(encoding="utf-8")
    assert re.search(rf"^root_package\s*=\s*{package.name}\s*$", text, re.M)
    assert re.search(r"^include_external_packages\s*=\s*True\s*$", text, re.M | re.I)
    assert len(re.findall(r"^\[importlinter:contract:", text, re.M)) == 1
    assert re.search(r"^type\s*=\s*forbidden\s*$", text, re.M)
    assert re.search(r"^forbidden_modules\s*=\s*\n?\s*pytest\s*$", text, re.M)


def test_new_project_is_green_on_architecture_right_after_init(tmp_path: Path) -> None:
    """Плохой пример: пусто-зелёный прогон. Контракт должен быть реально проверен."""
    init(tmp_path)
    done = parch_check(tmp_path, "architecture")
    assert done.returncode == 0, f"{done.stdout}{done.stderr}"
    assert "Правила архитектуры соблюдены" in done.stdout


def test_import_pytest_in_the_generated_package_breaks_the_architecture_contract(
    tmp_path: Path,
) -> None:
    """Плохой пример для контракта init: тестовый каркас в рабочем коде."""
    init(tmp_path)
    (new_package(tmp_path) / "bad.py").write_text("import pytest\n\nX = pytest\n", "utf-8")
    done = parch_check(tmp_path, "architecture")
    assert done.returncode == 1, f"{done.stdout}{done.stderr}"
    assert "pytest" in done.stdout


def test_single_module_instead_of_a_package_is_red_with_a_hint(tmp_path: Path) -> None:
    """Плохой пример: src/convert.py одиночным файлом, root_package = convert."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "convert.py").write_text("X = 1\n", encoding="utf-8")
    init(tmp_path)
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\nroot_package = convert\ninclude_external_packages = True\n\n"
        "[importlinter:contract:no-pytest]\nname = no pytest\ntype = forbidden\n"
        "source_modules =\n    convert\nforbidden_modules =\n    pytest\n",
        encoding="utf-8",
    )
    done = parch_check(tmp_path, "architecture")
    assert done.returncode == 1, f"{done.stdout}{done.stderr}"
    assert "корневой пакет должен быть пакетом" in (done.stdout + done.stderr).lower()


def test_init_on_a_project_with_code_creates_no_package_and_no_contract(tmp_path: Path) -> None:
    """Плохой пример: пакет-заготовка рядом с чужим кодом и правило, которое владелец не выбирал."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    result = init(tmp_path)
    assert [p.name for p in (tmp_path / "src").iterdir()] == ["app.py"]
    assert not (tmp_path / ".importlinter").exists()
    modules = (tmp_path / "docs" / "MODULES.md").read_text(encoding="utf-8")
    assert "src/app" not in modules and "| src/" not in modules
    assert result["created"]


def test_init_adds_the_package_row_to_modules_and_the_modules_check_is_green(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    package = new_package(tmp_path)
    modules = (tmp_path / "docs" / "MODULES.md").read_text(encoding="utf-8")
    assert len(re.findall(rf"src/{package.name}\b", modules)) == 1
    done = parch_check(tmp_path, "modules")
    assert done.returncode == 0, f"{done.stdout}{done.stderr}"


def test_init_twice_keeps_the_contract_and_does_not_duplicate_the_modules_row(
    tmp_path: Path,
) -> None:
    init(tmp_path)
    package = new_package(tmp_path)
    config = (tmp_path / ".importlinter").read_bytes()
    modules = (tmp_path / "docs" / "MODULES.md").read_bytes()
    init_file = (package / "__init__.py").read_bytes()
    second = init(tmp_path)
    assert (tmp_path / ".importlinter").read_bytes() == config
    assert (package / "__init__.py").read_bytes() == init_file
    assert new_package(tmp_path) == package
    assert (tmp_path / "docs" / "MODULES.md").read_bytes() == modules
    assert modules.decode("utf-8").count(f"src/{package.name}") == 1
    # Плохой пример: собственный пакет init засчитан как «код пользователя», проект новый
    # превращается в существующий (признак existing_project, предупреждения вместо ошибок).
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert "existing_project" not in baseline
    for note in second["notes"]:
        assert "уже есть код" not in note and "existing_project" not in note, note


def test_init_twice_after_the_user_added_a_module_treats_the_project_as_existing(
    tmp_path: Path,
) -> None:
    """Код пользователя в пакете делает проект существующим; пакет и контракт не перезаписаны."""
    init(tmp_path)
    package = new_package(tmp_path)
    logic = package / "logic.py"
    logic.write_text("def total(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    config = (tmp_path / ".importlinter").read_bytes()
    init_file = (package / "__init__.py").read_bytes()
    modules = (tmp_path / "docs" / "MODULES.md").read_bytes()
    init(tmp_path)
    assert (tmp_path / ".importlinter").read_bytes() == config
    assert (package / "__init__.py").read_bytes() == init_file
    assert "def total" in logic.read_text(encoding="utf-8")
    assert (tmp_path / "docs" / "MODULES.md").read_bytes() == modules
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["existing_project"] is True


def test_python_next_to_typescript_code_still_gets_a_package_and_a_contract(
    tmp_path: Path,
) -> None:
    """Плохой пример: код TypeScript в папке отменяет пакет Python, хотя Python-кода ещё нет."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    result = init(tmp_path, languages=["python", "typescript"])
    packages = [p for p in (tmp_path / "src").iterdir() if (p / "__init__.py").is_file()]
    assert len(packages) == 1, list(tmp_path.joinpath("src").iterdir())
    package = packages[0]
    assert (tmp_path / "src" / "index.ts").is_file()
    text = (tmp_path / ".importlinter").read_text(encoding="utf-8")
    assert re.search(rf"^root_package\s*=\s*{package.name}\s*$", text, re.M)
    assert re.search(r"^forbidden_modules\s*=\s*\n?\s*pytest\s*$", text, re.M)
    modules = (tmp_path / "docs" / "MODULES.md").read_text(encoding="utf-8")
    assert len(re.findall(rf"src/{package.name}\b", modules)) == 1
    for note in result["notes"]:
        assert "Пакет и .importlinter не созданы" not in note, note


def test_init_never_overwrites_an_existing_importlinter(tmp_path: Path) -> None:
    mine = "[importlinter]\nroot_package = mine\n# правило владельца\n"
    (tmp_path / ".importlinter").write_text(mine, encoding="utf-8")
    init(tmp_path)
    assert (tmp_path / ".importlinter").read_text(encoding="utf-8") == mine


def test_deleting_the_smoke_test_is_caught_in_the_generated_project(tmp_path: Path) -> None:
    init(tmp_path)
    (tmp_path / "tests" / "test_smoke.py").write_text(
        "def test_other() -> None:\n    assert True\n", encoding="utf-8"
    )
    done = parch_check(tmp_path, "tests")
    assert done.returncode == 1
    assert "tests/test_smoke.py::test_smoke" in done.stdout


def test_tool_versions_in_generated_requirements_match_this_repository() -> None:
    """Версии инструментов проверки зафиксированы и едины: в шаблоне и в CI самого продукта."""
    spec = importlib.util.spec_from_file_location("init_project_module", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    generated: str = module.REQUIREMENTS_DEV
    mine = {
        line.split("==")[0]: line
        for line in (REPO / "requirements-dev.txt").read_text(encoding="utf-8").split()
    }
    for line in generated.split():
        assert "==" in line, f"версия не зафиксирована: {line}"
        assert mine[line.split("==")[0]] == line


def test_generated_project_blocks_skips_suppressions_and_weaker_settings(tmp_path: Path) -> None:
    init(tmp_path)
    smoke = tmp_path / "tests" / "test_smoke.py"
    original = smoke.read_text(encoding="utf-8")
    body = original.split("\n\n\n", 1)[1]
    smoke.write_text("import pytest\n\n\n@pytest.mark.skip\n" + body, encoding="utf-8")
    assert parch_check(tmp_path, "skips").returncode == 1
    smoke.write_text(original, encoding="utf-8")
    assert parch_check(tmp_path, "skips").returncode == 0
    smoke.write_text(original + "\n\nX: int = 'a'  # type: ignore\n", encoding="utf-8")
    assert parch_check(tmp_path, "suppressions").returncode == 1
    smoke.write_text(original, encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    weaker = text.replace('typeCheckingMode = "strict"', 'typeCheckingMode = "off"')
    pyproject.write_text(weaker, encoding="utf-8")
    assert parch_check(tmp_path, "settings").returncode == 1


def test_generated_initial_baseline_records_settings_and_counts(tmp_path: Path) -> None:
    init(tmp_path)
    baseline = json.loads((tmp_path / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["skips"]["python"] == {}
    assert baseline["suppressions"]["python"] == {}
    keys = set(baseline["config"]["python"])
    assert {"pyproject.toml#tool.ruff", "pyproject.toml#tool.pyright"} <= keys
    assert "pyproject.toml#tool.pytest" in keys
