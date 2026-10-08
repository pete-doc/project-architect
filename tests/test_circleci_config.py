# ruff: noqa: E501
"""CI на CircleCI (ADR-0020): единственный CI репозитория, структура `.circleci/`.

В тестах нет YAML-библиотеки (её нет в зависимостях разработки), поэтому структура проверяется по тексту
конфигов без комментариев; синтаксис проверяет `circleci config validate` (запуск описан в ADR-0020).
Скрипт состава PR из setup-конфига выполняется по-настоящему во временных git-репозиториях. У каждого
правила есть «плохой пример», который проверка обязана остановить.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SETUP_RAW = (REPO / ".circleci" / "config.yml").read_text(encoding="utf-8")
CONTINUE_RAW = (REPO / ".circleci" / "continue_config.yml").read_text(encoding="utf-8")
HOOK_TESTS = ("tests/test_guards.py", "tests/test_launcher.py", "tests/test_flow.py")
NOT_MAIN = "- not:\n            equal: [main, << pipeline.git.branch >>]"


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
    if not re.search(r"ignore:\s*\n\s*- status\s*$", block(setup, "workflows:")):
        found.append("запуск не исключает ветку status")
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
    if "<< pipeline.parameters.hooks >>" not in block(workflows, "  windows:"):
        found.append("windows-hooks без условия «PR меняет hooks»")
    if "windows-hooks" in block(workflows, "  full:"):
        found.append("windows-hooks в workflow full")
    for name in ("text-only", "full", "windows", "no-windows"):
        if NOT_MAIN not in block(workflows, f"  {name}:"):
            found.append(f"workflow {name} идёт и на main")
    for name in ("install-dev", "full-tests", "check", "check-text", "windows-hooks", "state"):
        section = "commands:" if name in ("install-dev", "full-tests") else "jobs:"
        for step in re.split(r"\n      - ", block(block(cont, section), f"  {name}:")):
            if ("pytest" in step or "pip install" in step) and "no_output_timeout" not in step:
                found.append(f"{name}: долгий шаг без no_output_timeout: {step.splitlines()[0]}")
    tests = block(block(cont, "commands:"), "  full-tests:")
    if 'timeout 1500 pytest -v -n 4 -m "" --junitxml=test-report.xml' not in tests:
        found.append(
            "full-tests: тесты без общего предела времени (timeout 1500), без -n 4 или без полного режима"
        )
    for store in ("store_test_results", "store_artifacts"):
        if not re.search(rf"- {store}:\n\s+when: always\n\s+path: test-report\.xml", tests):
            found.append(f"full-tests: {store} без when: always или с другим путём отчёта")
    if "--partial" in cont:
        found.append("полный прогон не принимает урезанный отчёт (--partial)")
    if "WaitForExit(900000)" not in block(jobs, "  windows-hooks:"):
        found.append("windows-hooks: нет общего предела 15 минут на тесты")
    return found


def test_the_real_configs_have_no_problems() -> None:
    assert problems(SETUP, CONTINUE) == []


def test_branch_status_is_not_built_and_main_runs_only_the_state_board() -> None:
    assert "запуск не исключает ветку status" in problems(
        SETUP.replace("- status", "- other"), CONTINUE
    )
    bad = CONTINUE.replace(NOT_MAIN, "- not: true")  # ни у одного workflow нет «не main»
    assert {"workflow full идёт и на main", "workflow windows идёт и на main"} <= set(
        problems(SETUP, bad)
    )
    state = block(block(CONTINUE, "workflows:"), "  state:")
    assert "equal: [main, << pipeline.git.branch >>]" in state and "- state" in state


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
    bad = CONTINUE.replace("<< pipeline.parameters.hooks >>", "true", 1)
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
        "          when: always\n          path: test-report.xml", "          path: test-report.xml"
    )
    assert any("when: always" in p for p in problems(SETUP, bad))


def test_the_full_run_does_not_accept_a_partial_report() -> None:
    assert any(
        "--partial" in p
        for p in problems(SETUP, CONTINUE + "\n      - run: parch_ci.py tests --partial")
    )


def test_every_pr_gets_the_checks_the_ruleset_requires() -> None:
    # ruleset ветки main (ADR-0020): `ci/circleci: check` и `ci/circleci: windows-hooks` обязаны быть у каждого PR
    workflows = block(CONTINUE, "workflows:")
    assert "- check-text:\n          name: check" in block(workflows, "  text-only:")
    assert "      - check\n" in block(workflows, "  full:") + "\n"
    assert "- windows-hooks" in block(workflows, "  windows:")
    assert "- skip-windows:\n          name: windows-hooks" in block(workflows, "  no-windows:")


def test_the_state_board_is_published_only_from_main_and_never_prints_the_token() -> None:
    state = block(block(CONTINUE, "jobs:"), "  state:")
    assert "git push" in state and ":status" in state and "HEAD:main" not in state
    assert (
        'CIRCLE_BRANCH:-}" != "main"' in state and "CIRCLE_PR_NUMBER" in state
    )  # не ветка main или сборка PR
    assert (
        "STATUS_PUSH_TOKEN" in state
        and "echo $STATUS_PUSH_TOKEN" not in state
        and "set -x" not in state
    )
    assert (
        "--report-same-tree yes" in state and "timeout 1500 pytest" in state
    )  # отчёт снят на этом коммите
    # токен приходит только через context, подключённый к одному заданию state (ограничение context
    # владелец делает в CircleCI, ADR-0020); конфиг PR-заданий его не называет и context не подключает
    workflows = block(CONTINUE, "workflows:")
    assert "context: status-publisher" in block(workflows, "  state:")
    assert CONTINUE.count("context:") == 1
    assert "STATUS_PUSH_TOKEN" not in block(block(CONTINUE, "jobs:"), "  check:")
    # гонка двух слияний и потеря отчёта в новой ветке status
    assert "git ls-remote origin refs/heads/main" in state
    assert "cp test-report.xml /tmp/test-report.xml" in state
    assert "+refs/heads/status:refs/remotes/origin/status" in state


def test_the_windows_job_runs_the_same_hook_tests_as_before() -> None:
    windows = block(CONTINUE, "  windows-hooks:")
    for test in HOOK_TESTS:
        assert f'"{test}"' in windows, test
        assert (REPO / test).is_file(), test


def test_the_full_check_runs_every_check_the_old_ci_ran() -> None:
    job = block(block(CONTINUE, "jobs:"), "  check:")
    for expected in (
        "run: ruff check .",
        "run: ruff format --check .",
        "parch_ci.py standard",
        "run: pyright",
        "- full-tests",
        "- install-tools",
    ):
        assert expected in job, expected
    for check in ("tests", "skips"):
        assert f"parch_ci.py {check} --language python --report test-report.xml" in job
    assert job.index("- full-tests") < job.index("parch_ci.py tests --language python --report")


def test_every_cache_is_restored_and_saved() -> None:
    for key in ("pip-v1-", "npm-v1-", "dotnet-v1-", "pwsh-v1-7.4.6-psa-1.25.0", "pip-win-v1-"):
        assert CONTINUE.count(f"- {key}") >= 1 and CONTINUE.count(f"key: {key}") >= 1, key
    assert "-RequiredVersion 1.25.0" in CONTINUE  # версия PSScriptAnalyzer та же, что в шаблоне CI


def test_a_text_only_pr_runs_only_the_small_standard_job() -> None:
    job = block(block(CONTINUE, "jobs:"), "  check-text:")
    assert "resource_class: small" in job and "parch_ci.py standard" in job and "pytest" not in job


# ---------- скрипт состава PR выполняется по-настоящему ----------


def scope_script(raw: str = SETUP_RAW, step: str = "Состав PR") -> str:
    """Текст команды шага конфига (по имени шага), готовый к запуску в bash."""
    lines = raw.splitlines()
    start = next(i for i, line in enumerate(lines) if f"name: {step}" in line)
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
        env={**os.environ, "PARCH_SCOPE_OUT": out.as_posix()}, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr + done.stdout
    return json.loads((out / "params.json").read_text(encoding="utf-8"))


def run_repo_step(tmp_path: Path, origin: str) -> tuple[subprocess.CompletedProcess[str], str]:
    """Шаг state «Адрес репозитория на GitHub» в временном репозитории с заданным origin."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "remote", "add", "origin", origin)
    env_file = tmp_path / "bash_env"
    script = tmp_path / "repo.sh"
    state_steps = block(block(CONTINUE_RAW, "jobs:"), "  state:")
    script.write_text(
        scope_script(state_steps, "Адрес репозитория на GitHub (по origin)"),
        encoding="utf-8",
        newline="\n",
    )
    bash = shutil.which("bash")
    assert bash, "нужен bash"
    done = subprocess.run(
        [bash, "repo.sh"], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "BASH_ENV": env_file.as_posix(), "CIRCLE_PROJECT_USERNAME": "Peteru"}, timeout=60,
    )  # fmt: skip
    return done, env_file.read_text(encoding="utf-8") if env_file.exists() else ""


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/pete-doc/project-architect.git",
        "https://github.com/pete-doc/project-architect",
        "https://github.com/pete-doc/project-architect.git/",
        "https://github.com/pete-doc/project-architect/",
        "git@github.com:pete-doc/project-architect.git",
        "https://x-access-token:secret@github.com/pete-doc/project-architect.git",
        "ssh://git@github.com/pete-doc/project-architect.git",
        "ssh://git@github.com:22/pete-doc/project-architect.git",
        "http://github.com/pete-doc/project-architect.git",
    ],
)
def test_the_state_job_finds_the_github_repository_by_origin_not_by_circleci_names(
    tmp_path: Path, origin: str
) -> None:
    # CIRCLE_PROJECT_USERNAME/REPONAME в CircleCI это имена проекта в CircleCI (Peteru/ProjectArchitect),
    # а не адрес репозитория: первый прогон state упал на «repository not found»
    done, env_text = run_repo_step(tmp_path, origin)
    assert done.returncode == 0, done.stderr + done.stdout
    assert 'export GITHUB_REPO="pete-doc/project-architect"' in env_text
    assert "secret" not in done.stdout + done.stderr  # токен из origin никогда не печатается


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:secret@example.com/owner/repo.git",  # другой хост
        "https://user:secret@example.com/repo.git",  # другой хост, два сегмента
        "https://user:secret@github.com/onlyowner",  # нет имени репозитория
        "https://user:secret@github.com/a/b/c.git",  # лишний сегмент
        "git@gitlab.com:owner/repo.git",
        "https://user:secret@notgithub.com/owner/repo",
    ],
)
def test_the_state_job_stops_on_an_origin_it_does_not_recognise_and_never_shows_its_secret(
    tmp_path: Path, origin: str
) -> None:
    done, env_text = run_repo_step(tmp_path, origin)
    assert done.returncode != 0 and "GITHUB_REPO" not in env_text
    assert "secret" not in done.stdout + done.stderr and "example.com" not in done.stdout


def test_state_inputs_reports_a_missing_or_malformed_github_repo(tmp_path: Path) -> None:
    for value in ("", "onlyowner", "a/b/c"):
        out = tmp_path / f"prs-{len(value)}.json"
        done = subprocess.run(
            [sys.executable, str(REPO / ".circleci" / "state_inputs.py"), str(out)],
            env={**os.environ, "GITHUB_REPO": value, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )  # fmt: skip
        assert done.returncode == 0 and "GITHUB_REPO не задан" in done.stderr, value
        assert json.loads(out.read_text(encoding="utf-8")) == []


def test_the_state_job_takes_the_repository_only_from_github_repo() -> None:
    state = block(block(CONTINUE, "jobs:"), "  state:")
    assert "CIRCLE_PROJECT_USERNAME" not in state and "CIRCLE_PROJECT_REPONAME" not in state
    assert "github.com/${GITHUB_REPO}.git" in state
    inputs = (REPO / ".circleci" / "state_inputs.py").read_text(encoding="utf-8")
    assert 'environ.get("CIRCLE_PROJECT' not in inputs and 'environ.get("GITHUB_REPO"' in inputs


def test_a_text_only_pr_has_no_code_and_no_windows(tmp_path: Path) -> None:
    assert run_scope(tmp_path, ["docs/BACKLOG.md", "AGENTS.md"]) == {"code": False, "hooks": False}


def test_a_pr_that_changes_hooks_runs_windows_and_code(tmp_path: Path) -> None:
    assert run_scope(tmp_path, ["plugin/hooks/guard_paths.py"]) == {"code": True, "hooks": True}


def test_a_pr_that_changes_the_circleci_config_runs_everything(tmp_path: Path) -> None:
    result = run_scope(tmp_path, [".circleci/continue_config.yml", "docs/BACKLOG.md"])
    assert result == {"code": True, "hooks": True}


def test_non_ascii_file_names_are_not_lost(tmp_path: Path) -> None:
    assert run_scope(tmp_path, ["plugin/hooks/хук.py", "docs/план.md"]) == {
        "code": True,
        "hooks": True,
    }


def test_when_the_base_branch_is_unreachable_everything_runs(tmp_path: Path) -> None:
    result = run_scope(tmp_path, ["docs/BACKLOG.md"], with_origin=False)
    assert result == {"code": True, "hooks": True}


def test_a_pr_with_only_tests_runs_code_but_not_windows(tmp_path: Path) -> None:
    assert run_scope(tmp_path, ["tests/test_status.py"]) == {"code": True, "hooks": False}


# ---------- ADR-0020, правила и GitHub Actions ----------


def test_the_adr_names_the_windows_class_the_large_class_and_the_ruleset_checks() -> None:
    adr = next((REPO / "docs" / "adr").glob("0020-*.md")).read_text(encoding="utf-8")
    for fact in (
        "windows.medium",
        "win/server-2022",
        "`large`",
        "400 000",
        "Auto-cancel",
        "`ci/circleci: check`",
        "`ci/circleci: windows-hooks`",
        "режим совместимости",
    ):
        assert fact in adr, fact
    assert "- Статус: accepted" in adr


def test_agents_md_names_the_circleci_checks_and_the_old_flow_is_gone() -> None:
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "`ci/circleci: check`" in text and "`ci/circleci: windows-hooks`" in text
    assert "через `gh api`" in text and "запрещено" in text
    assert "gh pr ready --undo" not in text and "gh pr create --draft" not in text
    assert "любые файлы правил проверок" in text  # файлы правил проверок считаются кодом
    assert "`.circleci/`" in text.split("Условия, все сразу:", 1)[1].split("3.", 1)[1][:80]
    assert len(text.splitlines()) <= 150


# ---------- parch_ci standard читает .circleci/ ----------

CIRCLE_GOOD = """version: 2.1
jobs:
  check:
    docker:
      - image: cimg/python:3.12
    resource_class: small
    steps:
      - run:
          name: Тесты
          no_output_timeout: 25m
          command: timeout 1500 pytest -v
workflows:
  w:
    jobs:
      - check
"""
ACCEPTED_ADR = "- Статус: accepted\n\nИсполнитель `win/server-2022`, класс `large`.\n"
CIRCLE_PARCH = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"


def standard_on(folder: Path, config: str, adr: str = "") -> subprocess.CompletedProcess[str]:
    """Пустой проект с одним конфигом CircleCI (и принятым или нет ADR): запуск `standard`."""
    (folder / ".circleci").mkdir(parents=True)
    (folder / ".circleci" / "config.yml").write_text(config, encoding="utf-8")
    (folder / "docs").mkdir()
    constitution = "# CONSTITUTION\n\n## Целевая ОС\n\nWindows 11\n\n## Бюджеты\n\nБюджет инцидентов на блок: 2\n"
    (folder / "docs" / "CONSTITUTION.md").write_text(constitution, encoding="utf-8")
    for rel in ("docs/GOAL.md", "docs/MODULES.md", "docs/INCIDENT_TEMPLATE.md"):
        (folder / rel).write_text("x\n", encoding="utf-8")  # полный состав подключённого проекта
    (folder / "state").mkdir()
    (folder / "state" / "features.json").write_text(
        '{"version": 1, "features": []}', encoding="utf-8"
    )
    if adr:
        (folder / "docs" / "adr").mkdir()
        (folder / "docs" / "adr" / "0001-x.md").write_text(adr, encoding="utf-8")
    return subprocess.run(
        ["python", str(CIRCLE_PARCH), "standard", "--project", str(folder)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_standard_reads_circleci_and_a_clean_config_passes(tmp_path: Path) -> None:
    done = standard_on(tmp_path, CIRCLE_GOOD)
    assert done.returncode == 0, done.stdout
    assert "конфигов CircleCI 1" in done.stdout and "Auto-cancel Redundant Workflows" in done.stdout


def test_standard_stops_a_schedule_a_windows_executor_and_a_large_class_without_an_adr(
    tmp_path: Path,
) -> None:
    cases = {
        "schedule": (
            CIRCLE_GOOD + "triggers:\n  - schedule:\n      cron: '0 0 * * *'\n",
            "по расписанию",
        ),
        "windows": (
            CIRCLE_GOOD.replace("docker:", "executor: win/server-2022\n    docker:"),
            "win/server-2022",
        ),
        "large": (CIRCLE_GOOD.replace("resource_class: small", "resource_class: large"), "large"),
    }
    for name, (bad, word) in cases.items():
        done = standard_on(tmp_path / name, bad)
        assert done.returncode == 1 and word in done.stdout, name


def test_standard_accepts_them_when_an_accepted_adr_names_them_and_not_a_proposed_one(
    tmp_path: Path,
) -> None:
    config = CIRCLE_GOOD.replace("docker:", "executor: win/server-2022\n    docker:").replace(
        "resource_class: small", "resource_class: large"
    )
    assert standard_on(tmp_path / "ok", config, ACCEPTED_ADR).returncode == 0
    proposed = ACCEPTED_ADR.replace("accepted", "proposed")
    assert standard_on(tmp_path / "proposed", config, proposed).returncode == 1


def test_standard_stops_a_circleci_test_step_without_a_time_limit(tmp_path: Path) -> None:
    bad = CIRCLE_GOOD.replace("          no_output_timeout: 25m\n", "").replace("timeout 1500 ", "")
    done = standard_on(tmp_path, bad)
    assert done.returncode == 1 and "долгий шаг без предела времени" in done.stdout


def test_github_actions_of_the_repository_are_gone_but_the_scope_script_and_templates_stay() -> (
    None
):
    assert not (REPO / ".github" / "workflows").exists()
    assert (REPO / ".github" / "scope.py").is_file()  # его вызывает setup-конфиг CircleCI
    templates = {p.name for p in (REPO / "plugin" / "templates" / "ci").glob("*.yml")}
    assert "state.yml" not in templates  # шаблон табло на Actions заменён заданием state (ADR-0022)
    assert (REPO / "plugin" / "templates" / "ci" / "circleci" / "state" / "tail.yml").is_file()
    circle = {p.name for p in (REPO / "plugin" / "templates" / "ci" / "circleci").glob("*.yml")}
    assert {"head.yml", "python.yml", "typescript.yml", "csharp.yml", "powershell.yml"} <= circle
