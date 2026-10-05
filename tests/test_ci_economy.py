"""Экономия квоты CI (ADR-0011): Ubuntu по умолчанию, таймауты, отмена, запуск только по PR.

Workflow разбирается как текст: так тесты не зависят от лишних пакетов, а плохой пример
(матрица, macOS, push в main, нет таймаута) ловится прямо по строкам файла.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / ".circleci" / "config.yml"  # CI продукта живёт на CircleCI (ADR-0020)
PRODUCT = REPO / ".circleci" / "continue_config.yml"
SCOPE = REPO / ".github" / "scope.py"  # какие языки и проверки нужны PR
# state.yml (пересчёт табло после слияния, ADR-0012) устроен иначе: см. tests/test_status.py
ALL_TEMPLATES = sorted((REPO / "plugin" / "templates" / "ci").glob("*.yml"))
TEMPLATES = [p for p in ALL_TEMPLATES if p.name != "state.yml"]
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
    # CircleCI: расписаний нет; на main идёт только пересчёт табло (workflow state)
    both = code_only(text(SETUP)) + code_only(text(PRODUCT))
    assert "schedule" not in both and "triggers:" not in both
    assert re.search(r"ignore:\n\s+- status\b", code_only(text(SETUP)))
    workflows = code_only(text(PRODUCT)).split("\nworkflows:\n", 1)[1]
    not_main = "- not:\n            equal: [main, << pipeline.git.branch >>]"
    for name in ("text-only", "full", "windows", "no-windows"):
        assert not_main in job_block(workflows, name), name
    state = job_block(workflows, "state")
    assert "equal: [main, << pipeline.git.branch >>]" in state and "not:" not in state


def test_product_ci_cancels_superseded_runs() -> None:
    # отмена устаревших прогонов в CircleCI это настройка проекта, не конфига (ADR-0020):
    # из репозитория её проверить нельзя, поэтому `standard` каждый раз напоминает о ней
    adr = text(REPO / "docs" / "adr" / "0020-perehod-ci-s-github-actions-na-circleci.md")
    assert "Auto-cancel Redundant Workflows" in adr
    script = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
    done = subprocess.run(
        [sys.executable, str(script), "standard", "--project", str(REPO)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0 and "Auto-cancel Redundant Workflows" in done.stdout


def test_product_ci_has_no_matrix_and_no_macos() -> None:
    workflow = code_only(text(PRODUCT)) + code_only(text(SETUP))
    assert "matrix" not in workflow
    assert "macos" not in workflow.lower()
    assert "strategy:" not in workflow


def test_every_product_job_has_a_timeout() -> None:
    # у CircleCI нет timeout-minutes на задание: no_output_timeout и общий предел на тесты
    workflow = text(PRODUCT)
    assert "timeout 1500 pytest" in job_block(workflow, "full-tests")  # шаг команды full-tests
    assert "timeout 1500 pytest" in job_block(workflow, "state")
    assert "WaitForExit(900000)" in job_block(workflow, "windows-hooks")
    assert "no_output_timeout: 3m" in text(SETUP)
    for name in ("install-dev", "full-tests", "check", "check-text", "windows-hooks", "state"):
        for step in re.split(r"\n      - ", job_block(code_only(workflow), name)):
            if ("pytest" in step or "pip install" in step) and "no_output_timeout" not in step:
                raise AssertionError(f"{name}: долгий шаг без no_output_timeout: {step[:60]}")


def test_the_main_job_is_one_job_on_ubuntu_with_every_current_step() -> None:
    # одна проверка на PR и она же полный прогон (ADR-0020): шаги прежних check и full-run
    workflow = code_only(text(PRODUCT))
    block = job_block(workflow, "check")
    assert "image: cimg/python:3.12-node" in block  # Linux с Node из образа
    for step in (
        "run: ruff check .",
        "run: ruff format --check .",
        "run: pyright",
        "- install-tools",
    ):
        assert step in block, step
    for step in (
        'pytest -v -n 4 -m "" --junitxml=test-report.xml',
        "Install-Module -Name PSScriptAnalyzer -RequiredVersion 1.25.0",
        "--jsonfile tests/projects/cs_shop/global.json",
    ):
        assert step in workflow, step
    ratchet = "parch_ci.py tests --language python --report test-report.xml"
    assert "parch_ci.py skips --language python --report test-report.xml" in block
    assert block.index("- full-tests") < block.index(ratchet)
    assert "--partial" not in workflow  # полный прогон не принимает урезанный отчёт


def test_dotnet_sdk_and_the_powershell_module_are_cached() -> None:
    workflow = text(PRODUCT)
    assert "~/.dotnet" in workflow and "~/.nuget/packages" in workflow
    assert 'checksum "/tmp/dotnet-key.txt"' in workflow  # global.json и все packages.lock.json
    assert "~/.local/share/powershell/Modules" in workflow
    assert "pwsh-v1-7.4.6-psa-1.25.0" in workflow and "npm-v1-" in workflow


def test_a_docs_and_state_only_pr_runs_only_the_structure_checks() -> None:
    # правки только текста: идёт один standard на маленьком контейнере, без тестов (scope.py)
    scope_text = text(SCOPE)  # только эти пути не делают PR «кодовым»; baseline.json и прочее код
    assert '"state/incidents/*"' in scope_text and '"state/baseline.json"' not in scope_text
    block = job_block(code_only(text(PRODUCT)), "check-text")
    assert "resource_class: small" in block and "parch_ci.py standard" in block
    for heavy in ("pytest", "pyright", "ruff", "pip install"):
        assert heavy not in block, heavy


def test_if_the_pr_files_cannot_be_read_everything_runs() -> None:
    scope = text(SETUP)
    assert "Не удалось получить список файлов PR: идут проверки всех языков." in scope
    assert (
        "scope.py --unknown" in scope
    )  # скрипт при неизвестном списке включает всё (test_ci_split)


def test_windows_runs_only_the_hooks_tests_and_only_when_hooks_change() -> None:
    workflow = code_only(text(PRODUCT))
    block = job_block(workflow, "windows-hooks")
    assert "executor: win/server-2022" in block
    for test in HOOK_TESTS:
        assert f'"{test}"' in block, test
    for slow in ("dotnet", "pwsh", "pyright", "node"):
        assert slow not in block, slow
    assert "<< pipeline.parameters.hooks >>" in job_block(workflow, "windows")
    hooks = 'HOOKS = ("plugin/hooks/*", "plugin/templates/*", ".github/*")'
    assert hooks in text(SCOPE)


def test_windows_is_used_by_the_hooks_job_alone() -> None:
    workflow = code_only(text(PRODUCT))
    assert workflow.count("executor: win/") == 1


def test_hook_test_files_exist_and_are_the_files_that_run_the_hooks() -> None:
    for rel in HOOK_TESTS:
        source = text(REPO / rel)
        assert "run_hook" in source or "HOOKS" in source, rel


# ---------- шаблоны CI для проектов пользователей ----------


def test_templates_are_found() -> None:
    assert {p.name for p in ALL_TEMPLATES} == {
        "csharp.yml", "powershell.yml", "python.yml", "state.yml", "typescript.yml",
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
