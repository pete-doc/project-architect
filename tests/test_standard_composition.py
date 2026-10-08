# ruff: noqa: E501
"""F14, PR 2: связность реестра блоков, «Основания» в PR, отчёты об инцидентах в долге, защита файла долга.

Плохие примеры, которые проверка обязана остановить: блок без цели, зависимость от несуществующего блока, цикл,
«готово» без тестов приёмки, цель вне документа цели, пустые «Основания» и «Основания» без источника, новый отчёт
об инциденте без разделов, запись агента в долг. Исторические отчёты существующего проекта идут под долг владельца.
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_standard_full import SCRIPT, WORKFLOW, managed, standard, write

REPO = Path(__file__).resolve().parents[1]

GOOD_BASIS = "## Основания\n\n- Поиск по коду: «parch_standard» — нашёл правила состава.\n"
REPORT_OK = (
    "# Сбой\n\n## Влияние на цель\n\nКритерий G1 не затронут.\n\n## Где искал\n\n"
    '- Поиск по коду: «имя»: ничего.\n- История изменений: `git log -S"имя"`: пусто.\n'
)
DEBT = "state/standard-baseline.json"


def standard_pr(project: Path, body: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    """Запуск `standard` так, как его запускает CI: описание PR в переменной окружения."""
    env = {k: v for k, v in os.environ.items() if k != "PARCH_PR_BODY"}
    if body is not None:
        env["PARCH_PR_BODY"] = body
    return subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def put_features(root: Path, *blocks: dict[str, object]) -> None:
    write(root / "state" / "features.json", json.dumps({"version": 1, "features": list(blocks)}))


def block(bid: str, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": bid,
        "title": bid,
        "goal": ["G1"],
        "depends_on": [],
        "acceptance_tests": ["tests/test_a.py"],
        "status": "planned",
    }
    return {**base, **fields}


def test_a_block_without_a_goal_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    put_features(root, block("F1", goal=[]))
    done = standard(root)
    assert done.returncode == 1 and "блок F1: нет цели" in done.stdout


def test_a_dependency_on_a_missing_block_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    put_features(root, block("F1", depends_on=["F9"]))
    done = standard(root)
    assert done.returncode == 1 and "несуществующего блока F9" in done.stdout


def test_a_dependency_cycle_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    put_features(root, block("F1", depends_on=["F2"]), block("F2", depends_on=["F1"]))
    done = standard(root)
    assert done.returncode == 1 and "цикл зависимостей" in done.stdout


def test_a_done_block_needs_acceptance_tests_or_the_owner_acceptance(tmp_path: Path) -> None:
    root = managed(tmp_path)
    put_features(root, block("F1", status="done", acceptance_tests=[]))
    done = standard(root)
    assert done.returncode == 1 and "«готово» без тестов приёмки" in done.stdout
    write(
        root / "state" / "acceptance" / "F1.md",
        "# Приёмка F1\n\n- [x] критерий\n\nИтог: принято владельцем, 2026-10-05\n",
    )
    assert standard(root).returncode == 0  # блок без тестов, принятый владельцем


def test_a_goal_missing_in_the_goal_document_fails_when_the_document_names_goals(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path)
    write(root / "docs" / "GOAL.md", "# Цель\n\nG1: проект работает\n")
    put_features(root, block("F1", goal=["G7"]))
    done = standard(root)
    assert done.returncode == 1 and "цели G7 нет в документе цели" in done.stdout
    put_features(root, block("F1", goal=["G1"]))
    assert standard(root).returncode == 0


def test_a_coherent_registry_passes(tmp_path: Path) -> None:
    root = managed(tmp_path)
    put_features(root, block("F1"), block("F2", depends_on=["F1"], status="done"))
    assert standard(root).returncode == 0


def test_the_basis_section_of_the_pr_is_required_and_must_name_a_source(tmp_path: Path) -> None:
    root = managed(tmp_path)
    bad_bodies = {
        "нет раздела": "## Что сделано\n\nправка\n",
        "пусто": "## Основания\n\n## Как проверить\n\nтест\n",
        "только комментарий шаблона": "## Основания\n\n<!-- Каталог, поиск по коду, git log -->\n",
        "нет источника": "## Основания\n\nЯ всё проверил, поверьте.\n",
    }
    for title, body in bad_bodies.items():
        done = standard_pr(root, body)
        assert done.returncode == 1 and "Основания в PR" in done.stdout, title
    assert standard_pr(root, GOOD_BASIS).returncode == 0
    assert standard_pr(root, None).returncode == 0  # локальный запуск: описания PR нет


def test_the_basis_section_with_subheadings_and_code_blocks_is_read_whole(tmp_path: Path) -> None:
    root = managed(tmp_path)
    body = (
        "## Основания\n\n### Каталог\n\nСмотрел реестр модулей: нашёл parch_standard.\n\n"
        "### Поиск по коду\n\n```\n# это не заголовок\ngrep parch\n```\n\n## Как проверить\n\nтест\n"
    )
    assert standard_pr(root, body).returncode == 0
    only_subheadings = "## Основания\n\n### Каталог\n\n### Поиск\n\n## Как проверить\n\nтест\n"
    done = standard_pr(root, only_subheadings)
    assert done.returncode == 1 and "Основания в PR" in done.stdout


def test_a_second_dependency_cycle_is_not_hidden_by_a_recorded_one(tmp_path: Path) -> None:
    root = managed(tmp_path, existing=True)
    put_features(root, block("F1", depends_on=["F2"]), block("F2", depends_on=["F1"]))
    assert standard(root, "--update", "--accept-new").returncode == 0
    assert standard(root).returncode == 0  # записанный цикл допускается
    put_features(
        root,
        block("F1", depends_on=["F2"]),
        block("F2", depends_on=["F1"]),
        block("F3", depends_on=["F4"]),
        block("F4", depends_on=["F3"]),
    )
    done = standard(root)
    assert done.returncode == 1 and "блоками: F3, F4" in done.stdout
    assert "F1, F2" not in done.stdout  # записанный цикл остаётся допустимым


def test_a_pull_request_run_without_the_pr_text_warns_that_the_template_is_old(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "PARCH_PR_BODY"}
    env["GITHUB_EVENT_NAME"] = "pull_request"
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert done.returncode == 0 and "PARCH_PR_BODY не передан" in done.stdout


def test_the_basis_rule_on_an_existing_project_warns_until_the_debt_is_recorded(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    done = standard_pr(root, "## Что сделано\n\nправка\n")
    assert done.returncode == 0 and "Предупреждение" in done.stdout
    standard(root, "--update", "--accept-new")  # долг записан (пустой): храповик включён
    assert standard_pr(root, "## Что сделано\n\nправка\n").returncode == 1


def historic_reports(root: Path) -> None:
    write(root / "state" / "incidents" / "2026-09-02_old_crash.md", "# старый сбой\n")
    write(root / "state" / "incidents" / "2026-09-03_other.md", "# другой\n")


def test_a_new_project_checks_every_incident_report_fully(tmp_path: Path) -> None:
    root = managed(tmp_path)
    historic_reports(root)
    done = standard(root)
    assert done.returncode == 1 and "state/incidents/2026-09-02_old_crash.md" in done.stdout


def test_historic_reports_of_an_existing_project_are_warned_then_go_into_the_debt(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    historic_reports(root)
    done = standard(root)
    assert done.returncode == 0 and "Предупреждение" in done.stdout
    assert standard(root, "--update", "--accept-new").returncode == 0
    debt = json.loads((root / DEBT).read_text(encoding="utf-8"))
    assert debt["violations"] == [
        "incident:2026-09-02_old_crash.md",
        "incident:2026-09-03_other.md",
    ]
    assert standard(root).returncode == 0  # исторические отчёты в долге проходят


def test_a_new_incident_report_is_still_checked_fully_when_a_debt_exists(tmp_path: Path) -> None:
    root = managed(tmp_path, existing=True)
    historic_reports(root)
    standard(root, "--update", "--accept-new")
    new = root / "state" / "incidents" / "2026-10-05-NONE-new-crash.md"
    write(new, "# новый сбой без разделов\n")
    done = standard(root)
    assert done.returncode == 1 and "2026-10-05-NONE-new-crash.md" in done.stdout
    assert "old_crash" not in done.stdout  # исторические остаются допустимыми
    write(new, REPORT_OK)
    assert standard(root).returncode == 0  # новый отчёт в формате продукта проходит


def test_a_fixed_or_removed_historic_report_leaves_the_debt(tmp_path: Path) -> None:
    root = managed(tmp_path, existing=True)
    historic_reports(root)
    standard(root, "--update", "--accept-new")
    (root / "state" / "incidents" / "2026-09-03_other.md").unlink()
    assert standard(root, "--update").returncode == 0
    debt = json.loads((root / DEBT).read_text(encoding="utf-8"))
    assert debt["violations"] == ["incident:2026-09-02_old_crash.md"]


def test_an_unmanaged_project_is_still_checked_for_incident_reports_as_before(
    tmp_path: Path,
) -> None:
    write(tmp_path / ".github" / "workflows" / "ci.yml", WORKFLOW)
    write(tmp_path / "state" / "incidents" / "2026-09-02_old_crash.md", "# старый сбой\n")
    done = standard(tmp_path)
    assert done.returncode == 1 and "2026-09-02_old_crash.md" in done.stdout


def test_the_ci_templates_pass_the_pr_text_through_the_environment() -> None:
    for name in ("python.yml", "typescript.yml", "csharp.yml", "powershell.yml"):
        """Имя прежнее (храповик): в CircleCI описания PR нет, `standard` читает коммиты ветки (`PARCH_BASE_REF`)."""
        text = (REPO / "plugin" / "templates" / "ci" / "circleci" / name).read_text(
            encoding="utf-8"
        )
        assert "PARCH_BASE_REF: origin/main" in text and "PARCH_PR_BODY" not in text, name


# --- защита файла долга: его записывает только владелец (ADR-0019) ---


@pytest.mark.parametrize(
    "role", [None, "parch:implementer", "parch:architect", "parch:tester", "parch:reviewer"]
)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_an_agent_cannot_add_a_report_to_the_debt_it_needs_the_owner(
    tool: str, role: str | None, project: Path
) -> None:
    from conftest import file_call, run_hook

    result = run_hook("guard_paths.py", file_call(tool, project / DEBT), project, role)
    # tester правит только тесты и docs/QUESTIONS.md: блок раньше вопроса владельцу
    if role == "parch:tester":
        assert result.blocked and "Роль tester правит только тесты" in result.stdout + result.stderr
        return
    assert result.code == 0, result.stderr
    answer = json.loads(result.stdout)["hookSpecificOutput"]
    assert answer["permissionDecision"] == "ask"
    assert "Нужно подтверждение владельца" in answer["permissionDecisionReason"]
    assert DEBT in answer["permissionDecisionReason"]


def test_an_agent_cannot_append_to_the_debt_through_the_shell(project: Path) -> None:
    from conftest import bash, run_hook

    command = f"echo '\"incident:2026-10-05-NONE-new.md\"' >> {DEBT}"
    result = run_hook("guard_shell_writes.py", bash(command), project)
    assert result.blocked and "[guard_shell_writes]" in result.stderr


def test_the_debt_file_is_listed_as_owner_only_everywhere() -> None:
    settings = (REPO / "plugin" / "templates" / "claude" / "settings.json").read_text(
        encoding="utf-8"
    )
    assert "Edit(/state/standard-baseline.json)" in settings
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "`state/standard-baseline.json`" in agents and "добавленные в долг ключи" in agents
    adr = next((REPO / "docs" / "adr").glob("0019-*.md")).read_text(encoding="utf-8")
    assert "state/standard-baseline.json" in adr and "ADR-0003" in adr


# --- те же файлы долга каталога и модулей (ADR-0021) ---

OTHER_DEBTS = ("state/catalog-baseline.json", "state/modules-baseline.json")
ROLES = [None, "parch:implementer", "parch:architect", "parch:tester", "parch:reviewer"]


@pytest.mark.parametrize("rel", OTHER_DEBTS)
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_an_agent_cannot_add_a_key_to_the_catalog_or_modules_debt(
    rel: str, role: str | None, tool: str, project: Path
) -> None:
    from conftest import file_call, run_hook

    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, role)
    # tester правит только тесты и docs/QUESTIONS.md: блок раньше вопроса владельцу
    if role == "parch:tester":
        assert result.blocked and "Роль tester правит только тесты" in result.stdout + result.stderr
        return
    assert result.code == 0, result.stderr
    answer = json.loads(result.stdout)["hookSpecificOutput"]
    assert answer["permissionDecision"] == "ask"
    assert "Нужно подтверждение владельца" in answer["permissionDecisionReason"]
    assert rel in answer["permissionDecisionReason"]


@pytest.mark.parametrize("rel", OTHER_DEBTS)
def test_an_agent_cannot_append_to_the_catalog_or_modules_debt_through_the_shell(
    rel: str, project: Path
) -> None:
    from conftest import bash, run_hook

    command = f"echo '\"src/new.py::new_function\"' >> {rel}"
    result = run_hook("guard_shell_writes.py", bash(command), project)
    assert result.blocked and "[guard_shell_writes]" in result.stderr


def test_every_debt_file_the_checks_can_write_is_protected_and_listed() -> None:
    # страховка на будущее: любой новый `state/<имя>-baseline.json` в проверках обязан попасть под охрану
    hook = ast.parse((REPO / "plugin" / "hooks" / "guard_paths.py").read_text(encoding="utf-8"))
    values = [
        node.value
        for node in hook.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_STATE_FILES" for t in node.targets)
    ]
    assert len(values) == 1
    protected = {str(name) for name in ast.literal_eval(values[0])}
    names = {
        found
        for path in (REPO / "plugin" / "templates" / "ci" / "parch").glob("*.py")
        for found in re.findall(r"state/([\w-]+-baseline\.json)", path.read_text(encoding="utf-8"))
    }
    assert {"standard-baseline.json", "catalog-baseline.json", "modules-baseline.json"} <= names
    assert names - protected == set()
    settings = (REPO / "plugin" / "templates" / "claude" / "settings.json").read_text(
        encoding="utf-8"
    )
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    for name in sorted(names):
        assert f"Edit(/state/{name})" in settings, name
        assert f"`state/{name}`" in agents, name
    adr = next((REPO / "docs" / "adr").glob("0021-*.md")).read_text(encoding="utf-8")
    assert "catalog-baseline.json" in adr and "modules-baseline.json" in adr
    # весь список охраны назван и в условии 3 слияния PR: охраняемый, но не названный файл агент
    # мог бы слить сам (features.json меняется в каждом PR блока, его подтверждает охрана путей)
    condition = agents.split("Условия, все сразу:", 1)[1].split("4. Описание PR", 1)[0]
    for name in sorted(protected - {"features.json"}):
        assert f"state/{name}" in condition, name


def test_the_shell_is_also_stopped_by_the_path_guard_for_every_debt_file(project: Path) -> None:
    from conftest import bash, run_hook

    for rel in ("state/standard-baseline.json", *OTHER_DEBTS):
        for command in (f"rm {rel}", f"mv {rel} /tmp/x.json"):
            result = run_hook("guard_paths.py", bash(command), project)
            assert result.code == 0, result.stderr
            answer = json.loads(result.stdout)["hookSpecificOutput"]
            assert (
                answer["permissionDecision"] == "ask" and rel in answer["permissionDecisionReason"]
            )


# Шаг 0(д) F25: проект подключён по CONSTITUTION (как в hooks) или по копии скрипта проверок.


def without_parch_copy(root: Path) -> Path:
    (root / ".github" / "parch" / "parch_ci.py").unlink()
    (root / ".github" / "parch").rmdir()
    return root


def test_a_project_with_constitution_and_no_parch_copy_is_managed_and_missing_goal_fails(
    tmp_path: Path,
) -> None:
    root = without_parch_copy(managed(tmp_path))
    (root / "docs" / "GOAL.md").unlink()
    done = standard(root)
    assert done.returncode == 1 and "docs/GOAL.md" in done.stdout
    assert "не подключён" not in done.stdout + done.stderr


def test_a_complete_project_with_constitution_and_no_parch_copy_passes(tmp_path: Path) -> None:
    done = standard(without_parch_copy(managed(tmp_path)))
    assert done.returncode == 0, done.stdout


def test_a_project_with_neither_constitution_nor_parch_copy_is_not_managed_and_composition_is_skipped(
    tmp_path: Path,
) -> None:
    root = without_parch_copy(managed(tmp_path))
    (root / "docs" / "CONSTITUTION.md").unlink()
    (root / "docs" / "GOAL.md").unlink()
    done = standard(root)
    assert "не подключён" in done.stdout + done.stderr
    assert "docs/GOAL.md" not in done.stdout


def test_a_project_with_a_parch_copy_but_no_constitution_stays_managed(tmp_path: Path) -> None:
    root = managed(tmp_path)
    (root / "docs" / "CONSTITUTION.md").unlink()
    (root / "docs" / "GOAL.md").unlink()
    done = standard(root)
    assert done.returncode == 1 and "docs/GOAL.md" in done.stdout
    assert "не подключён" not in done.stdout + done.stderr


def test_constitution_in_the_project_root_without_docs_copy_also_makes_the_project_managed(
    tmp_path: Path,
) -> None:
    root = without_parch_copy(managed(tmp_path))
    text = (root / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    (root / "docs" / "CONSTITUTION.md").unlink()
    write(root / "CONSTITUTION.md", text)
    (root / "docs" / "GOAL.md").unlink()
    done = standard(root)
    assert done.returncode == 1 and "docs/GOAL.md" in done.stdout
    assert "не подключён" not in done.stdout + done.stderr
