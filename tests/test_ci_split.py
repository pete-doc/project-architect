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
CI = REPO / ".github" / "workflows" / "ci.yml"
FULL = REPO / ".github" / "workflows" / "full.yml"
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
        "docs/adr/0013-x.md", "docs/QUESTIONS.md", "state/incidents/2026-10-04-F4-loop.md",
        "state/acceptance/F17.md", "state/STATUS.md",
    )  # fmt: skip
    assert result["code"] == "false" and result["hooks"] == "false"
    assert result["selector"] == "not slow" and result["node"] == "false"


@pytest.mark.parametrize(
    "rule_file",
    [
        "state/baseline.json",
        "state/features.json",
        "state/future-rules.json",  # будущие файлы правил: в state/ по умолчанию код
        "docs/GOAL.md",
        "docs/MODULES.md",
        "docs/CONSTITUTION.md",
        "docs/rules/new-check.md",
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
        (
            "plugin/templates/ci/typescript.yml",
            "not slow or lang_typescript",
            "true",
            "false",
            "false",
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
    assert scope("plugin/templates/ci/state.yml")["hooks"] == "true"
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


def test_the_quick_run_never_gives_the_right_to_merge() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "statuses" not in text and "gh api" not in text  # никаких отметок вручную
    assert "-o junit_suite_name=parch-partial" in text and "--partial" in text
    assert 'pytest -v -m ""' not in text
    assert "full-run" not in text.replace(
        "(full.yml, проверка full-run:", ""
    )  # проверку даёт full.yml


def test_the_full_run_check_exists_only_after_ready_for_review() -> None:
    text = FULL.read_text(encoding="utf-8")
    on_block = text.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert on_block.strip() == "pull_request:\n    types: [ready_for_review]"  # других событий нет
    head = text.split("\n  full-run:\n", 1)[1].split("    steps:", 1)[0]
    assert "if:" not in head  # пропущенное по условию задание GitHub считает успешным
    assert "cancel-in-progress: true" in text and "group: full-${{ github.ref }}" in text


def test_no_workflow_sets_a_check_or_status_by_hand() -> None:
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "/statuses/" not in text and "-f context=" not in text, path.name
        assert "statuses: write" not in text and "checks: write" not in text, path.name


def test_the_full_run_covers_every_check_and_its_ratchet_runs_last() -> None:
    text = FULL.read_text(encoding="utf-8")
    for step in (
        "pytest -v -m",
        "parch_ci.py tests --language python --report test-report.xml",
        "parch_ci.py skips --language python --report test-report.xml",
        "pyright",
        "parch_ci.py standard",
    ):
        assert step in text, step
    assert text.index("pytest -v -m") < text.index("parch_ci.py tests --language python --report")
    assert "--partial" not in text  # полный прогон не принимает урезанный отчёт


def test_a_pr_without_code_still_gets_the_full_run_check_but_runs_no_tests() -> None:
    text = FULL.read_text(encoding="utf-8")
    assert "steps.scope.outputs.code != 'true'" in text and "scope.py --unknown" in text
    assert (
        text.count("steps.scope.outputs.code == 'true'") >= 8
    )  # установка и тесты только для кода


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


def test_agents_md_forbids_setting_checks_by_hand_and_explains_ready_for_review() -> None:
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "через `gh api`" in text and "запрещено" in text
    assert "источник проверок" in text and "GitHub Actions" in text
    assert "gh pr ready --undo" in text and "gh pr create --draft" in text
    assert "любые файлы правил проверок" in text  # файлы правил проверок считаются кодом
