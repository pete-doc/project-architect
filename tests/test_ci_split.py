# ruff: noqa: E501
"""Разделение CI (ADR-0013): быстрая часть на отправку, полный прогон перед слиянием.

Тяжёлые тесты языка идут в урезанном прогоне только при изменении файлов этого языка. Право на
слияние даёт один статус full-run, и выставляет его только полный прогон.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
SCOPE = REPO / ".github" / "scope.py"


def load_scope() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_scope", SCOPE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scope(*files: str) -> dict[str, str]:
    return load_scope().scope(list(files))


# ---------- какие тесты идут в урезанном прогоне ----------


def test_a_pr_with_only_decisions_questions_and_reports_has_no_code() -> None:
    result = scope(
        "AGENTS.md", "CLAUDE.md", "docs/BACKLOG.md", "docs/adr/0013-x.md", "docs/QUESTIONS.md",
        "docs/notes/plan.md", "state/incidents/2026-10-04-F4-loop.md", "state/acceptance/F17.md",
        "state/STATUS.md",
    )  # fmt: skip
    assert result["code"] == "false" and result["hooks"] == "false"
    assert result["selector"] == "not slow" and result["node"] == "false"


def test_text_files_that_checks_or_tests_read_still_count_as_code() -> None:
    for name in ("docs/GOAL.md", "docs/MODULES.md", "docs/CONSTITUTION.md", "CONSTITUTION.md"):
        assert scope("AGENTS.md", name)["code"] == "true", name
    assert scope("docs/BACKLOG.md", "docs/script.py")["code"] == "true"  # не текст


@pytest.mark.parametrize(
    "rule_file",
    [
        "state/baseline.json",
        "state/features.json",
        "state/future-rules.json",  # будущие файлы правил: в state/ по умолчанию код
        "docs/GOAL.md",
        "docs/MODULES.md",
        "docs/CONSTITUTION.md",
        "docs/STANDARD.md",  # его версию читает тест
    ],
)
def test_files_that_set_the_rules_of_checks_in_docs_and_state_count_as_code(rule_file: str) -> None:
    assert scope(rule_file)["code"] == "true"
    assert scope("docs/adr/0013-x.md", rule_file)["code"] == "true"  # хоть один такой файл


@pytest.mark.parametrize(
    ("changed", "selector", "node", "csharp", "powershell"),
    [
        ("tests/test_ci_csharp.py", "not slow or lang_csharp", "false", "true", "false"),
        ("tests/projects/cs_shop/global.json", "not slow or lang_csharp", "false", "true", "false"),
        pytest.param(
            "plugin/templates/ci/circleci/typescript.yml",
            "not slow or lang_typescript",
            "true",
            "false",
            "false",
            # id прежний (храповик не даёт переименовать тест): шаблон TypeScript теперь лежит в circleci/ (ADR-0022)
            id="plugin/templates/ci/typescript.yml-not slow or lang_typescript-true-false-false",
        ),
        ("tests/test_ci_powershell.py", "not slow or lang_powershell", "false", "false", "true"),
        ("tests/test_ci_python.py", "not slow or lang_python", "true", "false", "false"),
        ("plugin/hooks/guard_paths.py", "not slow", "false", "false", "false"),  # язык не менялся
        ("tests/test_status.py", "not slow", "false", "false", "false"),
    ],
)
def test_only_the_changed_language_gets_its_slow_tests(
    changed: str, selector: str, node: str, csharp: str, powershell: str
) -> None:
    result = scope(changed, "docs/x.md")
    assert result["selector"] == selector
    assert (result["node"], result["csharp"], result["powershell"]) == (node, csharp, powershell)
    assert result["code"] == "true"


@pytest.mark.parametrize(
    "shared",
    [
        "plugin/templates/ci/parch/parch_ci.py",
        "tests/conftest.py",
        "pyproject.toml",
        "requirements-dev.txt",
        ".github/workflows/ci.yml",
    ],
)
def test_shared_code_and_settings_run_every_language(shared: str) -> None:
    selector = scope(shared)["selector"]
    assert selector == (
        "not slow or lang_python or lang_typescript or lang_csharp or lang_powershell"
    )


def test_hooks_run_on_windows_only_when_hooks_templates_or_github_change() -> None:
    assert scope("plugin/hooks/pre_push.py")["hooks"] == "true"
    assert scope("plugin/templates/ci/circleci/state/tail.yml")["hooks"] == "true"
    assert scope(".github/workflows/full.yml")["hooks"] == "true"
    assert scope("tests/test_status.py", "docs/a.md")["hooks"] == "false"


def test_when_the_file_list_is_unknown_everything_runs() -> None:
    done = subprocess.run(
        [sys.executable, str(SCOPE), "--unknown"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert done.returncode == 0
    lines = dict(row.split("=", 1) for row in done.stdout.splitlines())
    assert lines["code"] == "true" and lines["hooks"] == "true"
    assert "lang_csharp" in lines["selector"] and "lang_powershell" in lines["selector"]


def test_the_scope_script_reads_the_file_list_from_standard_input() -> None:
    done = subprocess.run(
        [sys.executable, str(SCOPE)],
        input="tests/test_ci_csharp.py\ndocs/a.md\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert "selector=not slow or lang_csharp" in done.stdout


def test_every_language_test_file_has_a_marker_the_selector_can_use() -> None:
    conftest = (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    for language in ("python", "typescript", "csharp", "powershell"):
        assert f'"test_ci_{language}": "{language}"' in conftest, language
        assert f"lang_{language}:" in pyproject, language


# ---------- право на слияние: проверка full-run ----------


def test_no_workflow_sets_a_check_or_status_by_hand() -> None:
    # CI продукта на CircleCI (ADR-0020): ни один конфиг не ставит статусы и проверки вручную
    for path in sorted((REPO / ".circleci").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "/statuses/" not in text and "-f context=" not in text, path.name
        assert "check-runs" not in text and "gh api" not in text, path.name


def test_adr_0013_records_the_split_and_the_saving() -> None:
    adr = (REPO / "docs" / "adr" / "0013-razdelenie-ci.md").read_text(encoding="utf-8")
    for fact in (
        "full-run",
        "`ready_for_review`",
        "GitHub Actions",
        "вручную",
        "integration",
        "check`, `full-run` и",
        "parch-partial",
        "минут",
        "Экономия",
        "перед слиянием",
    ):
        assert fact in adr, fact
