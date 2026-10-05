# ruff: noqa: E501
"""CI на CircleCI (ADR-0020): структура `.circleci/` повторяет логику `.github/workflows/ci.yml`.

В тестах нет YAML-библиотеки (её нет в зависимостях разработки), поэтому структура проверяется по тексту
конфигов без комментариев; сам синтаксис проверяет `circleci config validate` (запуск описан в ADR-0020).
Скрипт состава PR из setup-конфига выполняется по-настоящему во временных git-репозиториях. У каждого
правила есть «плохой пример», который проверка обязана остановить.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP_RAW = (REPO / ".circleci" / "config.yml").read_text(encoding="utf-8")
CONTINUE_RAW = (REPO / ".circleci" / "continue_config.yml").read_text(encoding="utf-8")
HOOK_TESTS = ("tests/test_guards.py", "tests/test_launcher.py", "tests/test_flow.py")


def uncommented(text: str) -> str:
    """Конфиг без комментариев: закомментированный шаг не считается шагом."""
    return "\n".join(re.sub(r"(^|\s)#.*", "", line).rstrip() for line in text.splitlines())


SETUP = uncommented(SETUP_RAW)
CONTINUE = uncommented(CONTINUE_RAW)


def block(text: str, header: str) -> str:
    """Текст раздела верхнего уровня или задания (отступ в два пробела) до следующего такого же."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == header)
    indent = len(header) - len(header.lstrip())
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].lstrip()
        if stripped and len(lines[i]) - len(stripped) <= indent:
            end = i
            break
    return "\n".join(lines[start:end])


def problems(setup: str, cont: str) -> list[str]:
    found: list[str] = []
    if not re.search(r"^version: 2\.1$", setup, re.M) or not re.search(
        r"^setup: true$", setup, re.M
    ):
        found.append("setup-конфиг: нужны version 2.1 и setup: true")
    if not re.search(r"ignore:\s*\n\s*- main\s*\n\s*- status", block(setup, "workflows:")):
        found.append("запуск не исключает ветки main и status")
    scope = block(setup, "  scope:")
    if re.search(r"\bgh (pr|api|run)\b", scope):
        found.append("состав PR берётся через gh CLI, нужен git diff")
    if (
        "git -c core.quotePath=false diff --name-only" not in scope
        or ".github/scope.py --unknown" not in scope
    ):
        found.append("нет git diff с базовой веткой или запасного пути «все языки»")
    jobs = block(cont, "jobs:")
    windows_jobs = [
        name
        for name in re.findall(r"^  ([\w-]+):$", jobs, re.M)
        if "executor: win/" in block(jobs, f"  {name}:")
    ]
    if windows_jobs != ["windows-hooks"]:
        found.append(f"Windows используют не только hooks: {windows_jobs}")
    workflows = block(cont, "workflows:")
    if "when: << pipeline.parameters.hooks >>" not in block(workflows, "  windows:"):
        found.append("windows-hooks без условия «PR меняет hooks»")
    if "windows-hooks" in block(workflows, "  fast:"):
        found.append("windows-hooks в workflow fast")
    for name in ("check", "check-text", "windows-hooks"):
        for step in re.split(r"\n      - ", block(jobs, f"  {name}:")):
            if ("pytest" in step or "pip install" in step) and "no_output_timeout" not in step:
                found.append(f"{name}: долгий шаг без no_output_timeout: {step.splitlines()[0]}")
    check = block(jobs, "  check:")
    if "timeout 1500 pytest" not in check or "-n 4" not in check:
        found.append("check: тесты без общего предела времени (timeout 1500) или без -n 4")
    for store in ("store_test_results", "store_artifacts"):
        if not re.search(rf"- {store}:\n\s+when: always\n\s+path: test-report-partial\.xml", check):
            found.append(f"check: {store} без when: always или с другим путём отчёта")
    if (
        "--junitxml=test-report-partial.xml" not in check
        or check.count("--report test-report-partial.xml") != 2
    ):
        found.append("check: путь отчёта урезанного прогона расходится между шагами")
    if "WaitForExit(900000)" not in block(jobs, "  windows-hooks:"):
        found.append("windows-hooks: нет общего предела 15 минут на тесты")
    return found


def test_the_real_configs_have_no_problems() -> None:
    assert problems(SETUP, CONTINUE) == []


def test_branches_main_and_status_are_not_built() -> None:
    no_main = re.sub(r"- main\n", "", SETUP)
    assert "запуск не исключает ветки main и status" in problems(no_main, CONTINUE)
    assert "запуск не исключает ветки main и status" in problems(
        SETUP.replace("- status", "- other"), CONTINUE
    )


def test_the_pr_scope_must_not_use_the_gh_cli() -> None:
    bad = SETUP.replace(
        'cat "$out/scope.txt"', 'gh pr diff 1 --name-only\n            cat "$out/scope.txt"'
    )
    assert any("gh CLI" in p for p in problems(bad, CONTINUE))


def test_the_fallback_to_all_languages_exists() -> None:
    assert any("запасного пути" in p for p in problems(SETUP.replace("--unknown", ""), CONTINUE))


def test_windows_runs_only_in_the_hooks_job_and_only_when_hooks_change() -> None:
    bad = CONTINUE.replace(
        "  check-text:\n    docker:", "  check-text:\n    executor: win/server-2022\n    docker:"
    )
    assert any("Windows используют не только hooks" in p for p in problems(SETUP, bad))
    bad = CONTINUE.replace("when: << pipeline.parameters.hooks >>", "when: true")
    assert any("без условия" in p for p in problems(SETUP, bad))


def test_long_steps_have_a_no_output_timeout_and_a_total_limit() -> None:
    assert any(
        "без no_output_timeout" in p
        for p in problems(SETUP, CONTINUE.replace("no_output_timeout: 25m", ""))
    )
    assert any(
        "общего предела" in p for p in problems(SETUP, CONTINUE.replace("timeout 1500 ", ""))
    )
    assert any(
        "общего предела 15 минут" in p
        for p in problems(SETUP, CONTINUE.replace("WaitForExit(900000)", "WaitForExit()"))
    )


def test_the_test_report_is_stored_even_when_tests_fail() -> None:
    bad = CONTINUE.replace(
        "          when: always\n          path: test-report-partial.xml",
        "          path: test-report-partial.xml",
    )
    assert any("when: always" in p for p in problems(SETUP, bad))
    assert any(
        "расходится" in p
        for p in problems(
            SETUP, CONTINUE.replace("--junitxml=test-report-partial.xml", "--junitxml=report.xml")
        )
    )


def test_the_windows_job_runs_the_same_hook_tests_as_github_actions() -> None:
    github = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest -v " + " ".join(HOOK_TESTS) in github
    windows = block(CONTINUE, "  windows-hooks:")
    for test in HOOK_TESTS:
        assert f'"{test}"' in windows, test


def test_the_fast_job_runs_the_same_checks_as_github_actions() -> None:
    job = block(CONTINUE, "  check:")
    for expected in (
        "run: ruff check .",
        "run: ruff format --check .",
        "parch_ci.py standard",
        "run: pyright",
        "-o junit_suite_name=parch-partial",
        "parch_ci.py tests --language python --partial --report test-report-partial.xml",
        "parch_ci.py skips --language python --partial --report test-report-partial.xml",
    ):
        assert expected in job, expected


def test_every_cache_is_restored_and_saved() -> None:
    for key in ("pip-v1-", "npm-v1-", "dotnet-v1-", "pwsh-v1-7.4.6-psa-1.25.0", "pip-win-v1-"):
        assert CONTINUE.count(f"- {key}") >= 1 and CONTINUE.count(f"key: {key}") >= 1, key
    assert "-RequiredVersion 1.25.0" in CONTINUE  # версия PSScriptAnalyzer та же, что в ci.yml


def test_a_text_only_pr_runs_only_the_small_standard_job() -> None:
    job = block(CONTINUE, "  check-text:")
    assert "resource_class: small" in job and "parch_ci.py standard" in job and "pytest" not in job
    assert "not: << pipeline.parameters.code >>" in block(
        block(CONTINUE, "workflows:"), "  text-only:"
    )


# ---------- скрипт состава PR выполняется по-настоящему ----------


def scope_script() -> str:
    lines = SETUP_RAW.splitlines()
    start = next(i for i, line in enumerate(lines) if "name: Состав PR" in line)
    begin = next(i for i in range(start, len(lines)) if lines[i].strip() == "command: |") + 1
    body: list[str] = []
    for line in lines[begin:]:
        if line.strip() and not line.startswith(" " * 12):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip


def run_scope(tmp_path: Path, changed: list[str], with_origin: bool = True) -> dict[str, object]:
    """Временный репозиторий с веткой main на «сервере» и веткой PR; результат params.json."""
    origin, work, out = tmp_path / "origin.git", tmp_path / "work", tmp_path / "out"
    out.mkdir()
    work.mkdir()
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(work, "init", "-q", "-b", "main")
    (work / ".github").mkdir()
    shutil.copy(REPO / ".github" / "scope.py", work / ".github" / "scope.py")
    (work / "README.md").write_text("x\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature")
    for name in changed:
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
    if changed:
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "change")
    if not with_origin:
        git(work, "remote", "remove", "origin")
    bash = shutil.which("bash")
    assert bash, "для проверки скрипта состава PR нужен bash"
    script = work / "scope.sh"
    script.write_text(scope_script(), encoding="utf-8", newline="\n")
    done = subprocess.run(
        [bash, "scope.sh"], cwd=work, capture_output=True, text=True, encoding="utf-8",
        env={**__import__("os").environ, "PARCH_SCOPE_OUT": out.as_posix()}, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads((out / "params.json").read_text(encoding="utf-8"))


def test_a_text_only_pr_has_no_code_and_no_windows(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["docs/BACKLOG.md", "AGENTS.md"])
    assert result["code"] is False and result["hooks"] is False
    assert result["selector"] == "not slow"


def test_a_pr_that_changes_hooks_runs_windows_and_code(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["plugin/hooks/guard_paths.py"])
    assert result["code"] is True and result["hooks"] is True


def test_a_pr_that_changes_the_circleci_config_runs_everything(tmp_path: Path) -> None:
    result = run_scope(tmp_path, [".circleci/continue_config.yml", "docs/BACKLOG.md"])
    assert all(result[k] is True for k in ("code", "hooks", "node", "csharp", "powershell"))
    assert "lang_csharp" in str(result["selector"])


def test_non_ascii_file_names_are_not_lost(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["plugin/hooks/хук.py", "docs/план.md"])
    assert result["hooks"] is True and result["code"] is True


def test_when_the_base_branch_is_unreachable_every_language_runs(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["docs/BACKLOG.md"], with_origin=False)
    assert all(result[k] is True for k in ("code", "hooks", "node", "csharp", "powershell"))


def test_a_pr_with_one_language_selects_only_that_language(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["tests/projects/cs_shop/Program.cs"])
    assert result["csharp"] is True and result["powershell"] is False
    assert "lang_csharp" in str(result["selector"]) and "lang_powershell" not in str(
        result["selector"]
    )


def test_the_adr_names_the_windows_class_and_keeps_github_actions() -> None:
    adr = next((REPO / "docs" / "adr").glob("0020-*.md")).read_text(encoding="utf-8")
    for fact in ("windows.medium", "400 000", "Auto-cancel"):
        assert fact in adr, fact


@pytest.mark.parametrize("name", ["ci.yml", "full.yml", "state.yml"])
def test_github_actions_are_not_removed_yet(name: str) -> None:
    """Временный факт (ADR-0020): снять тест может только решение владельца о выключении GitHub Actions."""
    assert (REPO / ".github" / "workflows" / name).is_file()
