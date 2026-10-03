"""Экономия квоты CI (ADR-0011): Ubuntu по умолчанию, таймауты, отмена, запуск только по PR.

Workflow разбирается как текст: так тесты не зависят от лишних пакетов, а плохой пример
(матрица, macOS, push в main, нет таймаута) ловится прямо по строкам файла.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PRODUCT = REPO / ".github" / "workflows" / "ci.yml"
TEMPLATES = sorted((REPO / "plugin" / "templates" / "ci").glob("*.yml"))
HOOK_TESTS = ("tests/test_guards.py", "tests/test_launcher.py", "tests/test_flow.py")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_only(workflow: str) -> str:
    """Workflow без комментариев: в них честно названы запрещённые раннеры."""
    return "\n".join(row for row in workflow.splitlines() if not row.strip().startswith("#"))


def job_block(workflow: str, name: str) -> str:
    """Текст задания `name` (от его заголовка до следующего задания верхнего уровня)."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", workflow, re.M | re.S)
    assert match is not None, f"в workflow нет задания {name}"
    return match.group(1)


def triggers(workflow: str) -> str:
    match = re.search(r"^on:\n(.*?)(?=^\S|\Z)", workflow, re.M | re.S)
    assert match is not None, "в workflow нет раздела on"
    return match.group(1)


# ---------- CI продукта ----------


def test_product_ci_runs_only_on_pull_requests() -> None:
    trigger = triggers(text(PRODUCT))
    assert "pull_request" in trigger
    for forbidden in ("push", "schedule", "workflow_dispatch", "workflow_run"):
        assert forbidden not in trigger, forbidden


def test_product_ci_cancels_superseded_runs() -> None:
    workflow = text(PRODUCT)
    assert re.search(r"^concurrency:\n  group: ci-\$\{\{ github\.ref \}\}\n", workflow, re.M)
    assert "  cancel-in-progress: true\n" in workflow


def test_product_ci_has_no_matrix_and_no_macos() -> None:
    workflow = code_only(text(PRODUCT))
    assert "matrix" not in workflow
    assert "macos" not in workflow.lower()
    assert "strategy:" not in workflow


def test_every_product_job_has_a_timeout() -> None:
    workflow = text(PRODUCT)
    for name, minutes in (("scope", 3), ("check", 25), ("windows-hooks", 15)):
        assert f"timeout-minutes: {minutes}\n" in job_block(workflow, name), name


def test_the_main_job_is_one_job_on_ubuntu_with_every_current_step() -> None:
    workflow = text(PRODUCT)
    block = job_block(workflow, "check")
    assert "runs-on: ubuntu-latest" in block
    for step in (
        "ruff check .", "ruff format --check .", "pyright",
        'pytest -v -m "" --junitxml=test-report.xml',
        "parch_ci.py tests --language python --report test-report.xml",
        "parch_ci.py skips --language python --report test-report.xml",
        "Install-Module -Name PSScriptAnalyzer -RequiredVersion 1.25.0",
        "global-json-file: tests/projects/cs_shop/global.json", "actions/setup-node@v4",
    ):  # fmt: skip
        assert step in block, step
    ratchet = "parch_ci.py tests --language python --report"
    assert block.index("pytest -v -m") < block.index(ratchet)


def test_dotnet_sdk_and_the_powershell_module_are_cached() -> None:
    block = job_block(text(PRODUCT), "check")
    assert "path: ~/.dotnet" in block
    assert "hashFiles('tests/projects/cs_shop/global.json')" in block
    assert "path: ~/.local/share/powershell/Modules" in block
    assert "steps.psa-cache.outputs.cache-hit != 'true'" in block
    assert "cache: true" in block  # пакеты NuGet


def test_a_docs_and_state_only_pr_runs_only_the_structure_checks() -> None:
    workflow = text(PRODUCT)
    scope = job_block(workflow, "scope")
    assert "docs/*|state/*) ;;" in scope  # эти пути не делают PR «кодовым»
    block = job_block(workflow, "check")
    assert block.count("if: needs.scope.outputs.code != 'true'") == 2
    heavy = re.findall(r"if: needs\.scope\.outputs\.code == 'true'(?: && [^\n]+)?\n", block)
    assert len(heavy) >= 9  # node, dotnet (два шага), psa (три шага), pyright, pytest, ratchet
    assert "test_product_adr_index_is_up_to_date" in block
    assert "parch_ci.py tests --language python\n" in block  # без отчёта: baseline и тесты


def test_if_the_pr_files_cannot_be_read_everything_runs() -> None:
    scope = job_block(text(PRODUCT), "scope")
    assert "Не удалось получить список файлов PR: идут полные проверки." in scope
    assert scope.count("code=true") >= 2 and "hooks=true" in scope


def test_windows_runs_only_the_hooks_tests_and_only_when_hooks_change() -> None:
    workflow = text(PRODUCT)
    block = job_block(workflow, "windows-hooks")
    assert "runs-on: windows-latest" in block
    assert "if: needs.scope.outputs.hooks == 'true'" in block
    assert "pytest -v " + " ".join(HOOK_TESTS) in block
    for slow in ("dotnet", "pwsh", "pyright", "setup-node"):
        assert slow not in block, slow
    scope = job_block(workflow, "scope")
    assert "plugin/hooks/*|plugin/templates/*|.github/*) hooks=true" in scope


def test_windows_is_used_by_the_hooks_job_alone() -> None:
    workflow = code_only(text(PRODUCT))
    assert workflow.count("windows-latest") == 1


def test_hook_test_files_exist_and_are_the_files_that_run_the_hooks() -> None:
    for rel in HOOK_TESTS:
        source = text(REPO / rel)
        assert "run_hook" in source or "HOOKS" in source, rel


# ---------- шаблоны CI для проектов пользователей ----------


def test_templates_are_found() -> None:
    assert {p.name for p in TEMPLATES} == {
        "csharp.yml", "powershell.yml", "python.yml", "typescript.yml",
    }  # fmt: skip


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_runs_on_ubuntu_only_without_a_matrix(template: Path) -> None:
    workflow = code_only(text(template))
    assert re.findall(r"runs-on: (\S+)", workflow) == ["ubuntu-latest"]
    for forbidden in ("matrix", "strategy:", "windows", "macos"):
        assert forbidden not in workflow.lower(), forbidden


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_has_a_timeout_and_cancels_superseded_runs(template: Path) -> None:
    workflow = text(template)
    assert re.search(r"^    timeout-minutes: \d+$", workflow, re.M)
    assert re.search(r"^concurrency:\n  group: \$\{\{ github\.workflow \}\}-", workflow, re.M)
    assert "  cancel-in-progress: true\n" in workflow


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_runs_only_on_pull_requests(template: Path) -> None:
    trigger = triggers(text(template))
    assert "pull_request" in trigger
    for forbidden in ("push", "schedule", "workflow_dispatch"):
        assert forbidden not in trigger, forbidden


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_says_other_runners_need_an_adr_with_a_cost_estimate(
    template: Path,
) -> None:
    head = "\n".join(text(template).splitlines()[:10])
    assert "только через ADR" in head
    assert "Windows" in head and "x2" in head and "macOS" in head and "x10" in head


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_every_template_has_one_job_named_check(template: Path) -> None:
    workflow = text(template)
    assert re.findall(r"^  ([\w-]+):\n    runs-on", workflow, re.M) == ["check"]


# ---------- ADR-0011 ----------


def test_the_incident_adr_is_accepted_and_has_the_numbers() -> None:
    adr = text(REPO / "docs" / "adr" / "0011-ci-ubuntu-po-umolchaniyu.md")
    assert "- Статус: accepted" in adr
    for fact in ("2 700 минут", "17 долларов", "×10", "×2", "#10", "только через ADR"):
        assert fact in adr, fact
