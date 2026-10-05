# ruff: noqa: E501
"""F16, PR 1: роль `reviewer`, команда `/parch:review` и правило «ревью перед просьбой “сливай”».

Плохие примеры, которые проверка обязана остановить: роль с инструментами записи (ревьюер правил бы то, что ревьюит), роль
длиннее 40 строк, без описания, из списка ролей, которым разрешено править тесты; команда ревью без запуска агента в чистом
контексте; правило слияния без ревью.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path

import guard_paths
import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "plugin" / "agents" / "reviewer.md"
SKILL = ROOT / "plugin" / "skills" / "review" / "SKILL.md"
AGENTS_MD = ROOT / "AGENTS.md"
WRITING = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "первая строка файла должна быть ---"
    head = text.split("\n---\n", 1)[0].removeprefix("---\n")
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in head.splitlines())}


def role_problems(text: str) -> list[str]:
    """Нарушения требований к роли ревьюера: поля, длина, инструменты записи, роль тестов."""
    problems: list[str] = []
    if len(text.splitlines()) > 40:
        problems.append("длиннее 40 строк")
    fields = frontmatter(text)
    if not fields.get("description"):
        problems.append("нет description")
    if fields.get("name") != "reviewer":
        problems.append("имя не reviewer")
    tools = {t.strip() for t in fields.get("tools", "").split(",") if t.strip()}
    if tools & WRITING:
        problems.append(f"инструменты записи: {sorted(tools & WRITING)}")
    if fields.get("name") in guard_paths.TEST_ROLES:
        problems.append("роль из тех, кому разрешено править тесты")
    return problems


def test_the_real_reviewer_role_has_no_problems() -> None:
    assert role_problems(AGENT.read_text(encoding="utf-8")) == []


def with_write_tools(text: str) -> str:
    return text.replace(
        "tools: Read, Grep, Glob, Bash", "tools: Read, Grep, Glob, Bash, Edit, Write"
    )


def too_long(text: str) -> str:
    return text + "\nлишняя строка\n" * 10


def without_description(text: str) -> str:
    return re.sub(r"description: .*\n", "description:\n", text, count=1)


def under_a_test_role_name(text: str) -> str:
    return text.replace("name: reviewer", "name: tester")


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (with_write_tools, "инструменты записи"),
        (too_long, "длиннее 40 строк"),
        (without_description, "нет description"),
        (under_a_test_role_name, "имя не reviewer"),
    ],
)
def test_a_reviewer_role_that_breaks_the_rules_is_rejected(
    mutate: Callable[[str], str], expected: str
) -> None:
    problems = role_problems(mutate(AGENT.read_text(encoding="utf-8")))
    assert any(expected in p for p in problems), problems


def test_the_reviewer_tools_are_read_only_and_bash_is_guarded() -> None:
    fields = frontmatter(AGENT.read_text(encoding="utf-8"))
    tools = {t.strip() for t in fields["tools"].split(",")}
    assert not (tools & WRITING) and "Bash" in tools
    hooks = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    guarded: set[str] = set()
    for entry in hooks["hooks"]["PreToolUse"]:
        if any("guard_paths.py" in h["command"] for h in entry["hooks"]):
            guarded = set(entry["matcher"].split("|"))
    assert "Bash" in guarded  # запись через оболочку тоже под охраной путей


def test_the_role_reviews_only_correctness_and_the_spec_and_ignores_style() -> None:
    text = AGENT.read_text(encoding="utf-8")
    for must in (
        "корректность",
        "спецификаци",
        "стиль не обсуждаешь",
        "gh pr comment",
        "Замечаний нет",
    ):
        assert must in text, must


def skill_problems(text: str) -> list[str]:
    problems: list[str] = []
    fields = frontmatter(text)
    if fields.get("name") != "review":
        problems.append("имя не review")
    if "parch:reviewer" not in text or "Agent" not in text:
        problems.append("не запускает агента parch:reviewer инструментом Agent")
    if "чистом контексте" not in text:
        problems.append("не требует чистого контекста")
    if "по-русски" not in text:
        problems.append("не требует пересказа по-русски")
    return problems


def test_the_review_command_runs_the_agent_in_a_clean_context_and_retells_in_russian() -> None:
    assert skill_problems(SKILL.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("parch:reviewer", "кто-нибудь", "не запускает агента"),
        ("чистом контексте", "общем контексте", "не требует чистого контекста"),
        ("по-русски", "кратко", "не требует пересказа по-русски"),
    ],
)
def test_a_review_command_without_the_key_steps_is_rejected(
    old: str, new: str, expected: str
) -> None:
    text = SKILL.read_text(encoding="utf-8").replace(old, new)
    assert any(expected in p for p in skill_problems(text))


def test_agents_md_requires_the_review_before_asking_the_owner_to_merge() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "/parch:review" in text and "до просьбы «сливай»" in text
    assert len(text.splitlines()) <= 150  # бюджет текста для ИИ (раздел 12 стандарта)
