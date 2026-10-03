"""Разделение CI (ADR-0013): быстрая часть на отправку, полный прогон перед слиянием.

Тяжёлые тесты языка идут в урезанном прогоне только при изменении файлов этого языка. Право на
слияние даёт один статус full-run, и выставляет его только полный прогон.
"""

import importlib.util
import re
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


def test_a_docs_only_pr_has_no_code_and_runs_no_slow_tests() -> None:
    result = scope("docs/adr/0013-x.md", "state/baseline.json", "docs/GOAL.md")
    assert result["code"] == "false" and result["hooks"] == "false"
    assert result["selector"] == "not slow" and result["node"] == "false"


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


# ---------- право на слияние: статус full-run ----------


def test_the_quick_run_never_gives_the_right_to_merge() -> None:
    text = CI.read_text(encoding="utf-8")
    assert "statuses: write" in text
    pending = text.index("state=pending -f context=full-run")
    assert pending < text.index("actions/checkout@v4", pending)  # статус ставится до всех проверок
    assert text.count("state=success") == 1  # единственный успех: PR без кода
    block = text[text.index("Статус full-run (PR без кода)") :].split("\n      - ", 1)[0]
    assert "if: needs.scope.outputs.code != 'true'" in block
    assert "-o junit_suite_name=parch-partial" in text and "--partial" in text
    assert 'pytest -v -m ""' not in text


def test_only_the_full_run_reports_success_and_only_after_every_check() -> None:
    text = FULL.read_text(encoding="utf-8")
    success = text.index("state=success -f context=full-run")
    for step in (
        "pytest -v -m",
        "parch_ci.py tests --language python --report test-report.xml",
        "parch_ci.py skips --language python --report test-report.xml",
        "pyright",
    ):
        assert text.index(step) < success, step
    assert "state=failure -f context=full-run" in text
    assert re.search(r"Статус full-run провал\n\s+if: failure\(\)", text)


def test_the_full_run_starts_only_with_the_full_label() -> None:
    text = FULL.read_text(encoding="utf-8")
    assert "contains(github.event.pull_request.labels.*.name, 'full')" in text
    assert "github.event.label.name == 'full'" in text  # другая метка его не запускает
    on_block = text.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert "types: [opened, synchronize, reopened, labeled]" in on_block
    assert "push" not in on_block and "workflow_dispatch" not in on_block
    assert "cancel-in-progress: true" in text and "group: full-${{ github.ref }}" in text


def test_the_quick_run_ignores_labels_so_a_label_cannot_cancel_it() -> None:
    on_block = CI.read_text(encoding="utf-8").split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert "labeled" not in on_block


def test_adr_0013_records_the_split_and_the_saving() -> None:
    adr = (REPO / "docs" / "adr" / "0013-razdelenie-ci.md").read_text(encoding="utf-8")
    for fact in (
        "full-run",
        "метка `full`",
        "parch-partial",
        "минут",
        "Экономия",
        "перед слиянием",
    ):
        assert fact in adr, fact
