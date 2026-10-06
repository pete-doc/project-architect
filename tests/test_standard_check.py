# ruff: noqa: E501
"""Проверка `standard` (STANDARD.md, разделы 7.2 и 11): стоимость CI и бюджет текста.

На каждое правило есть заведомо плохой пример, который проверка обязана остановить.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

GOOD = """name: ci

on:
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - run: echo hi
"""


def project(root: Path, workflow: str = GOOD, name: str = "ci.yml", adr: str = "") -> Path:
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows" / name).write_text(workflow, encoding="utf-8", newline="\n")
    if adr:
        (root / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "adr" / "0001-runner.md").write_text(adr, encoding="utf-8")
    return root


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def passes(root: Path) -> str:
    done = run(root)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def fails(root: Path, *expected: str) -> str:
    done = run(root)
    assert done.returncode == 1, f"проверка должна была упасть: {done.stdout}{done.stderr}"
    assert "ПРОВАЛ" in done.stdout
    for text in expected:
        assert text in done.stdout, text
    return done.stdout


# ---------- хороший пример и сам продукт ----------


def test_a_workflow_with_every_required_setting_passes(tmp_path: Path) -> None:
    out = passes(project(tmp_path))
    assert "Стандарт 1.3" in out
    assert "Пока не проверяется" in out  # честно: остальное в разделе 11 ещё не проверяется


def test_the_product_itself_follows_the_standard() -> None:
    assert "ПРОВАЛ" not in passes(REPO)


CIRCLE_TEMPLATES = REPO / "plugin" / "templates" / "ci" / "circleci"
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"


def circleci_config(language: str) -> str:
    """`.circleci/config.yml`, который соберёт init для языка шаблона."""
    spec = importlib.util.spec_from_file_location("init_project_standard_check", INIT)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass в init_project ищет свой модуль здесь
    spec.loader.exec_module(module)
    return str(module.render_circleci([language]))


@pytest.mark.parametrize(
    "template",
    [
        *sorted((REPO / "plugin" / "templates" / "ci").glob("*.yml")),
        *sorted(p for p in CIRCLE_TEMPLATES.glob("*.yml") if p.name != "head.yml"),
    ],
    ids=lambda p: p.name,
)
def test_each_ci_template_passes_the_standard_check(tmp_path: Path, template: Path) -> None:
    """Шаблон Actions (state.yml, до PR 2 блока F24) идёт как workflow, шаблоны CircleCI как собранный init `.circleci/config.yml`."""
    if template.parent == CIRCLE_TEMPLATES:
        (tmp_path / ".circleci").mkdir()
        (tmp_path / ".circleci" / "config.yml").write_text(
            circleci_config(template.stem), encoding="utf-8", newline="\n"
        )
        passes(tmp_path)
    else:
        passes(project(tmp_path, template.read_text(encoding="utf-8"), template.name))


# ---------- таймаут ----------


def test_a_job_without_timeout_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace("    timeout-minutes: 20\n", "")
    fails(project(tmp_path, bad), "timeout-minutes не задан", "360 минут")


@pytest.mark.parametrize("minutes", ["0", "21", "360"])
def test_a_timeout_over_the_limit_is_rejected(tmp_path: Path, minutes: str) -> None:
    bad = GOOD.replace("timeout-minutes: 20", f"timeout-minutes: {minutes}")
    fails(project(tmp_path, bad), f"timeout-minutes {minutes}")


def test_one_job_without_timeout_among_several_is_found(tmp_path: Path) -> None:
    bad = GOOD + "  second:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    out = fails(project(tmp_path, bad), "задание second")
    assert "задание check" not in out


# ---------- concurrency ----------


def test_a_workflow_without_concurrency_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace(
        "concurrency:\n  group: ci-${{ github.ref }}\n  cancel-in-progress: true\n\n", ""
    )
    fails(project(tmp_path, bad), "нет concurrency с cancel-in-progress: true")


def test_concurrency_that_does_not_cancel_is_rejected(tmp_path: Path) -> None:
    bad = GOOD.replace("cancel-in-progress: true", "cancel-in-progress: false")
    fails(project(tmp_path, bad), "нет concurrency с cancel-in-progress: true")


# ---------- триггеры ----------


@pytest.mark.parametrize(
    "trigger",
    [
        "pull_request:\n  push:\n    branches: [main]",
        "pull_request:\n  schedule:\n    - cron: '0 3 * * *'",
        "pull_request:\n  workflow_dispatch:",
        "push:\n    branches: [main]",
        "pull_request_target:",
    ],
)
def test_triggers_other_than_pull_request_are_rejected(tmp_path: Path, trigger: str) -> None:
    bad = GOOD.replace("  pull_request:\n", f"  {trigger}\n", 1)
    fails(project(tmp_path, bad), "CI запускается только на pull_request")


@pytest.mark.parametrize("form", ["on: [push, pull_request]", "on: push", "on: pull_request"])
def test_inline_trigger_forms_are_understood(tmp_path: Path, form: str) -> None:
    bad = GOOD.replace("on:\n  pull_request:\n", form + "\n")
    if form == "on: pull_request":
        passes(project(tmp_path, bad))
    else:
        fails(project(tmp_path, bad), "push")


def test_only_the_state_workflow_may_run_on_push_to_main(tmp_path: Path) -> None:
    state = GOOD.replace("  pull_request:\n", "  push:\n    branches: [main]\n", 1)
    passes(project(tmp_path / "ok", state, "state.yml"))
    fails(project(tmp_path / "bad", state, "ci.yml"), "CI запускается только на pull_request")


# ---------- раннеры и матрицы ----------

WINDOWS = GOOD.replace("ubuntu-latest", "windows-latest")
ACCEPTED = "# ADR-0001. Windows\n\n- Статус: accepted\n\nРаннер windows-latest: оценка квоты x2.\n"


def test_a_non_linux_runner_without_an_adr_is_rejected(tmp_path: Path) -> None:
    fails(project(tmp_path, WINDOWS), "раннер windows-latest не разрешён", "x2")


def test_macos_without_an_adr_is_rejected(tmp_path: Path) -> None:
    fails(project(tmp_path, GOOD.replace("ubuntu-latest", "macos-latest")), "macos-latest", "x10")


def test_an_accepted_adr_that_names_the_runner_allows_it(tmp_path: Path) -> None:
    passes(project(tmp_path, WINDOWS, adr=ACCEPTED))


def test_a_proposed_adr_does_not_allow_a_runner(tmp_path: Path) -> None:
    proposed = ACCEPTED.replace("accepted", "proposed")
    fails(project(tmp_path, WINDOWS, adr=proposed), "windows-latest")


def test_an_adr_that_names_another_runner_does_not_help(tmp_path: Path) -> None:
    other = ACCEPTED.replace("windows-latest", "macos-latest")
    fails(project(tmp_path, WINDOWS, adr=other), "windows-latest")


def test_a_matrix_runner_is_rejected(tmp_path: Path) -> None:
    matrix = GOOD.replace(
        "    runs-on: ubuntu-latest\n",
        "    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-latest]\n"
        "    runs-on: ${{ matrix.os }}\n",
    )
    out = fails(project(tmp_path, matrix), "матрица умножает минуты")
    assert "runs-on" not in out or "${{ matrix.os }}" in out


PYTHON_MATRIX = GOOD.replace(
    "    runs-on: ubuntu-latest\n",
    "    strategy:\n      matrix:\n        python: ['3.11', '3.12']\n    runs-on: ubuntu-latest\n",
)


def test_a_matrix_on_ubuntu_alone_is_rejected_too(tmp_path: Path) -> None:
    fails(project(tmp_path, PYTHON_MATRIX), "матрица умножает минуты")


def test_a_matrix_is_allowed_only_by_an_adr_that_names_the_file(tmp_path: Path) -> None:
    matrix = PYTHON_MATRIX
    adr = "- Статус: accepted\n\nМатрица Python в ci.yml: 2 прогона по 17 минут.\n"
    passes(project(tmp_path, matrix, adr=adr))


def test_a_job_without_a_runner_is_rejected(tmp_path: Path) -> None:
    fails(project(tmp_path, GOOD.replace("    runs-on: ubuntu-latest\n", "")), "не задан runs-on")


# ---------- разбор и бюджет текста ----------


def test_a_workflow_that_cannot_be_read_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    fails(project(tmp_path, "name: ci\non: pull_request\n"), "не удалось разобрать")


def test_agents_md_over_the_budget_is_rejected(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "AGENTS.md").write_text("строка\n" * 151, encoding="utf-8")
    fails(root, "AGENTS.md: 151 строк, бюджет 150")


def test_agents_md_at_the_budget_passes(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "AGENTS.md").write_text("строка\n" * 150, encoding="utf-8")
    passes(root)


def test_every_violation_is_reported_not_only_the_first(tmp_path: Path) -> None:
    bad = GOOD.replace("    timeout-minutes: 20\n", "").replace("ubuntu-latest", "macos-latest")
    bad = bad.replace("  pull_request:\n", "  pull_request:\n  push:\n")
    out = fails(project(tmp_path, bad), "timeout-minutes не задан", "macos-latest", "push")
    assert "Нарушения стандарта 1.3 (3)" in out


def test_the_check_is_registered_for_every_language(tmp_path: Path) -> None:
    root = project(tmp_path)
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "standard",
            "--language",
            "powershell",
            "--project",
            str(root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == 0, done.stdout


# ---------- таймаут дольше 20 минут: только по принятому ADR ----------


def test_a_longer_timeout_is_allowed_only_when_an_accepted_adr_records_it(tmp_path: Path) -> None:
    long = GOOD.replace("timeout-minutes: 20", "timeout-minutes: 25")
    fails(project(tmp_path / "bad", long), "timeout-minutes 25")
    adr = "- Статус: accepted\n\nЗапас на плавающий раннер: `timeout-minutes: 25` для check.\n"
    passes(project(tmp_path / "ok", long, adr=adr))


def test_a_proposed_adr_does_not_allow_a_longer_timeout(tmp_path: Path) -> None:
    long = GOOD.replace("timeout-minutes: 20", "timeout-minutes: 25")
    adr = "- Статус: proposed\n\nЗапас: `timeout-minutes: 25`.\n"
    fails(project(tmp_path, long, adr=adr), "timeout-minutes 25")


def test_an_adr_for_one_value_does_not_allow_another(tmp_path: Path) -> None:
    long = GOOD.replace("timeout-minutes: 20", "timeout-minutes: 30")
    adr = "- Статус: accepted\n\nЗапас: `timeout-minutes: 25`.\n"
    fails(project(tmp_path, long, adr=adr), "timeout-minutes 30")


def test_no_timeout_above_sixty_minutes_is_ever_allowed(tmp_path: Path) -> None:
    huge = GOOD.replace("timeout-minutes: 20", "timeout-minutes: 61")
    adr = "- Статус: accepted\n\nЗапас: `timeout-minutes: 61`.\n"
    fails(project(tmp_path, huge, adr=adr), "timeout-minutes 61", "не больше 60")


def test_the_product_check_timeout_is_25_and_is_recorded_in_adr_0011() -> None:
    # CI продукта на CircleCI (ADR-0020): 25 минут это `timeout 1500` вокруг тестов
    workflow = (REPO / ".circleci" / "continue_config.yml").read_text(encoding="utf-8")
    assert "timeout 1500 pytest" in workflow
    adr = (REPO / "docs" / "adr" / "0011-ci-ubuntu-po-umolchaniyu.md").read_text(encoding="utf-8")
    assert "`timeout-minutes: 25`" in adr
    assert "ключи кэша не виноваты" in adr and "1,9 раза" in adr


# ---------- блоки «заблокирован» обязаны иметь отчёт об инциденте ----------


def features_file(root: Path, *items: tuple[str, str]) -> None:
    (root / "state").mkdir(exist_ok=True)
    features = [{"id": i, "status": status} for i, status in items]
    (root / "state" / "features.json").write_text(
        json.dumps({"version": 1, "features": features}), encoding="utf-8"
    )


@pytest.mark.parametrize("status", ["blocked", "stuck"])
def test_a_blocked_block_without_an_incident_report_fails(tmp_path: Path, status: str) -> None:
    root = project(tmp_path)
    features_file(root, ("F4", status))
    out = fails(root, "блок F4", status, "waiting_owner")
    assert "state/incidents/" in out


def test_a_blocked_block_with_its_own_incident_report_passes(tmp_path: Path) -> None:
    root = project(tmp_path)
    features_file(root, ("F4", "blocked"), ("F5", "waiting_owner"), ("F6", "planned"))
    (root / "state" / "incidents").mkdir()
    report = (
        "# Отчёт\n\n## Влияние на цель\n\nКритерий G1.\n\n"
        "## Где искал\n\n- Поиск по коду: «retry»\n"
        "- История изменений кода (`git log -S`): `git log -S retry`\n"
    )
    (root / "state" / "incidents" / "2026-10-04-F5-other.md").write_text(report, encoding="utf-8")
    fails(root, "блок F4")  # отчёт чужого блока не считается
    (root / "state" / "incidents" / "2026-10-04-F4-loop.md").write_text(report, encoding="utf-8")
    passes(root)  # waiting_owner и planned отчёта не требуют
