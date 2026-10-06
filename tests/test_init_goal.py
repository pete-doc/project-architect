# ruff: noqa: E501
"""/parch:init-project: цель продукта (GOAL.md), целевая ОС, фраза про бюджет Actions.

Разговор с владельцем доходит до проекта через ответы в JSON; без цели и без целевой ОС подключение
отказывает, чтобы эти два шага нельзя было пропустить.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import INIT_ANSWERS, HookResult, file_call, run_hook

REPO = Path(__file__).resolve().parent.parent
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
SKILL = REPO / "plugin" / "skills" / "init-project" / "SKILL.md"
BASE: dict[str, Any] = {
    "name": "Магазин",
    "languages": ["python"],
    "description": "Считает заказы.",
    "priorities": "надёжность",
}


def load_init() -> Any:
    spec = importlib.util.spec_from_file_location("init_project_goal", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_init(project: Path, **extra: Any) -> tuple[int, str, str]:
    import json

    request = {"project_dir": str(project), **BASE, **INIT_ANSWERS, **extra}
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=300,
    )
    return done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")


def init_ok(project: Path, **extra: Any) -> dict[str, Any]:
    import json

    code, out, err = run_init(project, **extra)
    assert code == 0, err
    result: dict[str, Any] = json.loads(out)
    return result


# ---------- GOAL.md ----------


def test_goal_md_is_created_from_the_conversation(tmp_path: Path) -> None:
    init_ok(tmp_path)
    goal = (tmp_path / "docs" / "GOAL.md").read_text(encoding="utf-8")
    assert "# GOAL — цель продукта «Магазин»" in goal
    assert "Статус: черновик, ждёт утверждения владельца" in goal
    assert "Считает заказы магазина." in goal
    assert "Продавцы небольшого магазина." in goal
    assert "- **G1.** Сумма заказа считается верно" in goal
    assert "- **G2.** Заказ можно посмотреть по номеру" in goal
    assert "- Оплата" in goal
    assert "{{" not in goal


def test_empty_out_of_scope_is_written_honestly(tmp_path: Path) -> None:
    answers: dict[str, Any] = {**INIT_ANSWERS["goal"], "out_of_scope": []}
    init_ok(tmp_path, goal=answers)
    goal = (tmp_path / "docs" / "GOAL.md").read_text(encoding="utf-8")
    assert "(владелец пока не назвал)" in goal


def test_empty_criteria_texts_are_dropped_and_numbers_stay_continuous(tmp_path: Path) -> None:
    answers: dict[str, Any] = {**INIT_ANSWERS["goal"], "criteria": ["первый", "  ", "второй"]}
    init_ok(tmp_path, goal=answers)
    goal = (tmp_path / "docs" / "GOAL.md").read_text(encoding="utf-8")
    assert "G2.** второй" in goal and "G3" not in goal


@pytest.mark.parametrize(
    ("goal", "word"),
    [
        (None, "нужна цель продукта"),
        ("просто текст", "нужна цель продукта"),
        ({"audience": "а", "criteria": ["к"]}, "goal.summary"),
        ({"summary": "с", "criteria": ["к"]}, "goal.audience"),
        ({"summary": "с", "audience": "а"}, "goal.criteria"),
        ({"summary": "с", "audience": "а", "criteria": []}, "goal.criteria"),
        ({"summary": "с", "audience": "а", "criteria": ["  "]}, "goal.criteria"),
        ({"summary": "с", "audience": "а", "criteria": "один критерий"}, "нужен список"),
        ({"summary": " ", "audience": "а", "criteria": ["к"]}, "goal.summary"),
    ],
)
def test_init_refuses_without_a_real_goal(tmp_path: Path, goal: Any, word: str) -> None:
    code, _, error = run_init(tmp_path, goal=goal)
    assert code == 1
    assert error.startswith("init-project:")
    assert word in error
    assert not (tmp_path / "docs" / "CONSTITUTION.md").exists()
    assert not (tmp_path / "docs" / "GOAL.md").exists()


def test_an_existing_goal_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "GOAL.md").write_text("своя цель\n", encoding="utf-8")
    result = init_ok(tmp_path)
    assert (tmp_path / "docs" / "GOAL.md").read_text(encoding="utf-8") == "своя цель\n"
    assert "docs/GOAL.md" in result["skipped_existing"]


def test_the_folders_for_specs_and_incidents_exist(tmp_path: Path) -> None:
    init_ok(tmp_path)
    assert (tmp_path / "docs" / "specs" / ".gitkeep").is_file()
    assert (tmp_path / "state" / "incidents" / ".gitkeep").is_file()


# ---------- целевая ОС ----------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("windows", "Windows 11"),
        ("Windows 11", "Windows 11"),
        ("WIN", "Windows 11"),
        ("macos", "macOS"),
        ("macOS", "macOS"),
        ("mac", "macOS"),
        ("linux", "Linux"),
        ("Linux", "Linux"),
    ],
)
def test_target_os_is_recorded_in_the_constitution(
    tmp_path: Path, answer: str, expected: str
) -> None:
    init_ok(tmp_path, target_os=answer)
    text = (tmp_path / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    section = text.split("## Целевая ОС\n", 1)[1].split("\n## ", 1)[0]
    assert section.strip().splitlines()[0] == expected
    assert "ubuntu-latest" in section  # объяснение: CI всегда на Linux
    assert "{{" not in text


@pytest.mark.parametrize("answer", [None, "", "  ", "amiga", "dos"])
def test_init_refuses_without_a_target_os(tmp_path: Path, answer: Any) -> None:
    code, _, error = run_init(tmp_path, target_os=answer)
    assert code == 1
    assert "target_os" in error
    assert not (tmp_path / "docs" / "CONSTITUTION.md").exists()


def test_normalize_target_os_unit() -> None:
    module = load_init()
    assert module.normalize_target_os("windows") == "Windows 11"
    with pytest.raises(ValueError, match="target_os"):
        module.normalize_target_os("beos")


# ---------- инструкция владельцу про CircleCI (пункты В1–В5, ADR-0022) ----------

OWNER_STEPS = (
    "В1. Доступ GitHub App CircleCI",
    "В2. Создать проект в CircleCI",
    "В3. Включить автоотмену",
    "В4. После первого зелёного `check` включить защиту main",
    "В5. Ключ записи табло, только после В4",
)


def test_the_budget_phrase_is_always_in_the_report(tmp_path: Path) -> None:
    """Имя прежнее (храповик): раньше здесь проверялась фраза про бюджет Actions, теперь инструкция В1–В5."""
    notes = init_ok(tmp_path)["notes"]
    phrase = next(n for n in notes if n.startswith("CI работает на CircleCI"))
    positions = [phrase.index(step) for step in OWNER_STEPS]
    assert positions == sorted(positions)  # строгий порядок: ключ записи после защиты main
    assert "30 000 кредитов в месяц" in phrase and "ci/circleci: check" in phrase
    assert "Actions" not in phrase and "PARCH_GITHUB_READ_TOKEN" not in phrase  # токена чтения нет
    assert "Никакие токены для проверки не нужны" in phrase


def test_the_budget_phrase_is_given_to_the_owner_by_the_skill() -> None:
    """Имя прежнее (храповик): навык передаёт владельцу инструкцию В1–В5 дословно и в том же порядке."""
    skill = SKILL.read_text(encoding="utf-8")
    assert "инструкцию про CircleCI" in skill and "дословно" in skill and "В1–В5" in skill
    assert "бюджет на GitHub Actions" not in skill


# ---------- разговор в навыке ----------


def test_the_skill_asks_about_the_target_os_and_the_goal() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    assert "целевая ОС" in skill and "Windows 11, macOS, Linux" in skill
    assert "## Шаг 2. Поговори о цели продукта" in skill
    for key in ('"target_os"', '"goal"', '"summary"', '"audience"', '"criteria"', '"out_of_scope"'):
        assert key in skill, key
    assert "Не больше пяти вопросов" in skill
    assert "docs/GOAL.md" in skill and "черновик" in skill


# ---------- проверка standard: целевая ОС записана ----------


def standard(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def test_a_project_made_by_init_passes_the_standard_target_os_rule(tmp_path: Path) -> None:
    init_ok(tmp_path)
    done = standard(tmp_path)
    assert done.returncode == 0, done.stdout
    assert "целевая ОС" in done.stdout


def constitution_without_os(tmp_path: Path, replacement: str) -> Path:
    init_ok(tmp_path)
    path = tmp_path / "docs" / "CONSTITUTION.md"
    text = path.read_text(encoding="utf-8")
    head, rest = text.split("## Целевая ОС\n", 1)
    _, tail = rest.split("\n## ", 1)
    path.write_text(head + replacement + "## " + tail, encoding="utf-8", newline="\n")
    return tmp_path


@pytest.mark.parametrize(
    "replacement",
    ["", "## Целевая ОС\n\n> только пояснение\n\n", "## Целевая ОС\n\n(не указана)\n\n"],
)
def test_standard_requires_a_target_os_in_the_constitution(
    tmp_path: Path, replacement: str
) -> None:
    project = constitution_without_os(tmp_path, replacement)
    done = standard(project)
    assert done.returncode == 1, done.stdout
    assert "не записана целевая ОС" in done.stdout


def test_standard_accepts_a_target_os_written_by_hand(tmp_path: Path) -> None:
    project = constitution_without_os(tmp_path, "## Целевая ОС\n\nLinux\n\n")
    assert standard(project).returncode == 0


def test_a_project_without_a_constitution_is_not_asked_for_a_target_os(tmp_path: Path) -> None:
    assert standard(tmp_path).returncode == 0


# ---------- GOAL.md защищён, как CONSTITUTION.md ----------


def asked_owner(result: HookResult) -> bool:
    return result.code == 0 and '"permissionDecision": "ask"' in result.stdout


def test_editing_goal_md_asks_the_owner(project: Path) -> None:
    (project / "docs" / "GOAL.md").write_text("цель\n", encoding="utf-8")
    result = run_hook("guard_paths.py", file_call("Edit", project / "docs" / "GOAL.md"), project)
    assert asked_owner(result), result.stderr + result.stdout
    assert "GOAL.md" in result.stdout


def test_a_goal_md_outside_docs_is_not_special(project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Edit", project / "src" / "GOAL.md"), project)
    assert not asked_owner(result)


def test_the_permissions_template_asks_for_goal_md() -> None:
    import json

    template = json.loads(
        (REPO / "plugin" / "templates" / "claude" / "settings.json").read_text(encoding="utf-8")
    )
    assert "Edit(/docs/GOAL.md)" in template["permissions"]["ask"]
