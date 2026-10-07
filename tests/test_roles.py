# ruff: noqa: E501
"""Роли агентов (2026-10-07): роль tester, согласованность ролей в hooks, шаблонах, навыках и STANDARD.md.

Живой прогон F20 (шаг 4) показал: правило охраны путей разрешало править тесты только ролям architect и tester,
а этих агентов в плагине не было, поэтому написать тест не мог никто. Здесь два вида проверок:
1. роль tester настоящая (файл агента) и охрана путей относится к ней и к другим ролям так, как заявлено;
2. любая роль, на которую ссылаются hooks, шаблоны, навыки и STANDARD.md, либо есть в `plugin/agents/`, либо названа
   в одной строке BACKLOG «РОЛИ БЕЗ АГЕНТА». Плохие примеры на подставном тексте: правило с несуществующей ролью красное.
"""

import re
from pathlib import Path

import pytest
from conftest import file_call, run_hook

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "plugin" / "agents"
BACKLOG = ROOT / "docs" / "BACKLOG.md"
MARKER = "РОЛИ БЕЗ АГЕНТА"
CANDIDATES = "architect|tester|implementer|reviewer|janitor|explorer|planner|researcher|designer|debugger|analyst|inspector"


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: первая строка файла роли должна быть ---"
    head = text.split("\n---\n", 1)[0].removeprefix("---\n")
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in head.splitlines())}


def agent_names() -> set[str]:
    return {frontmatter(path)["name"] for path in sorted(AGENTS.glob("*.md"))}


# ---------- роль tester ----------


def test_the_tester_role_file_has_the_fields_claude_code_requires() -> None:
    """Плохой пример: без name и description Claude Code молча пропускает агента (как было с отсутствующей ролью)."""
    fields = frontmatter(AGENTS / "tester.md")
    assert fields["name"] == "tester" and ":" not in fields["name"]
    assert fields["description"], "без description агента Claude Code пропускает молча"
    assert set(fields) <= {"name", "description", "tools", "model"}
    for ignored in ("hooks", "mcpServers", "permissionMode"):
        assert ignored not in fields  # плагин-агенты эти поля игнорируют
    assert {"Read", "Edit", "Write", "Bash"} <= {t.strip() for t in fields["tools"].split(",")}
    lines = (AGENTS / "tester.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 40, f"tester.md: {len(lines)} строк, предел 40 (STANDARD.md, раздел 12)"


def test_the_tester_instruction_says_what_the_standard_requires() -> None:
    """Пишет тесты по описанию блока, не подгоняет под код, код не правит, при необходимости правки кода останавливается."""
    text = (AGENTS / "tester.md").read_text(encoding="utf-8")
    for must in (
        "state/features.json",  # откуда блок и файл тестов
        "docs/specs/",
        "не угадывай",  # не определено в описании: вопрос, а не догадка
        "docs/QUESTIONS.md",
        "заведомо плохой пример",
        "Не правишь код блока",  # код не правит
        "остановись и сообщи",  # нужна правка кода: сообщает
        "state/baseline.json",  # baseline обновляет владелец
        "не ослабляют",  # тест не подгоняют и не ослабляют
    ):
        assert must in text, must
    assert "skip" in text and "xfail" in text  # пропуски запрещены


def test_guard_paths_lets_tester_write_tests_and_nobody_else(project: Path) -> None:
    """Для каждого агента плагина: правит тесты только tester (и architect, которого нет); остальные и основная сессия блокированы."""
    target = project / "tests" / "test_convert.py"
    for name in sorted(agent_names()):
        result = run_hook("guard_paths.py", file_call("Write", target), project, f"parch:{name}")
        if name == "tester":
            assert result.code == 0 and not result.blocked, (
                result.stderr
            )  # плохой пример для остальных ниже
        else:
            assert result.blocked and "architect и tester" in (result.stdout + result.stderr), name
    nobody = run_hook("guard_paths.py", file_call("Write", target), project, None)
    assert nobody.blocked and "основная сессия без роли" in (nobody.stdout + nobody.stderr)


def test_guard_paths_does_not_yet_stop_tester_from_editing_code(project: Path) -> None:
    """Известный пробел, записан в BACKLOG: охрана путей не умеет «tester не правит код», это держит только инструкция
    роли. Тест фиксирует нынешнее поведение, чтобы изменение охраны было осознанным (по слову владельца)."""
    code = project / "src" / "convert.py"
    result = run_hook("guard_paths.py", file_call("Write", code), project, "parch:tester")
    assert result.code == 0 and not result.blocked
    backlog = BACKLOG.read_text(encoding="utf-8")
    assert "tester" in backlog and "не умеет" in backlog  # пробел назван в BACKLOG


# ---------- согласованность ролей ----------


def roles_in_text(text: str) -> set[str]:
    """Роли, на которые ссылается текст: слова-кандидаты, «роль X», «роли X и Y», `*_ROLES = frozenset({...})`
    (`parch:X` не берём: так называются и агенты, и навыки)."""
    found = {m.lower() for m in re.findall(rf"\b({CANDIDATES})\b", text, re.IGNORECASE)}
    found |= {m.lower() for m in re.findall(r"\bрол[ьи]\s+[`«]?([a-z][a-z-]+)", text)}
    for group in re.findall(r"\bроли\s+[`«]?([a-z][a-z-]+)[`»]?\s+и\s+[`«]?([a-z][a-z-]+)", text):
        found |= set(group)
    for body in re.findall(r"_ROLES\s*=\s*frozenset\(\{([^}]*)\}\)", text):
        found |= set(re.findall(r'"([a-z][a-z-]+)"', body))
    return {role for role in found if role not in {"role", "roles"}}


def unimplemented(text: str, agents: set[str], marked: set[str]) -> set[str]:
    """Роли, упомянутые в тексте, которых нет ни среди агентов, ни в помеченных «пока не реализована»."""
    return roles_in_text(text) - agents - marked


def marked_roles(backlog: str) -> set[str]:
    lines = [line for line in backlog.splitlines() if MARKER in line]
    assert len(lines) == 1, (
        f"строка «{MARKER}» должна быть в BACKLOG ровно одна, найдено {len(lines)}"
    )
    tail = lines[0].split(":", 1)[1] if ":" in lines[0] else ""
    names = tail.split(".")[0]
    return {w.strip(" `") for w in names.split(",") if w.strip(" `")}


def scope_files() -> list[Path]:
    files = sorted((ROOT / "plugin" / "hooks").glob("*.py"))
    files += sorted((ROOT / "plugin" / "templates").rglob("*.md"))
    files += sorted((ROOT / "plugin" / "skills").rglob("SKILL.md"))
    files += sorted(AGENTS.glob("*.md"))
    files.append(ROOT / "docs" / "STANDARD.md")
    return files


def test_every_role_the_rules_mention_exists_or_is_marked_as_not_implemented() -> None:
    agents, marked = agent_names(), marked_roles(BACKLOG.read_text(encoding="utf-8"))
    problems = {
        path.relative_to(ROOT).as_posix(): sorted(
            unimplemented(path.read_text(encoding="utf-8"), agents, marked)
        )
        for path in scope_files()
    }
    problems = {path: roles for path, roles in problems.items() if roles}
    assert problems == {}, (
        f"правила ссылаются на роли без агента и без пометки в BACKLOG: {problems}"
    )


def test_a_rule_that_names_a_missing_role_is_caught() -> None:
    """Плохие примеры на подставном тексте: правило со ссылкой на несуществующую роль красное."""
    agents, marked = {"implementer", "reviewer", "tester"}, {"janitor"}
    assert unimplemented("Тесты правят только роли architect и tester.", agents, marked) == {
        "architect"
    }
    assert unimplemented('TEST_ROLES = frozenset({"architect", "tester"})', agents, marked) == {
        "architect"
    }
    assert unimplemented("Этот шаг делает роль `inspector`.", agents, marked) == {"inspector"}
    assert unimplemented("Агент parch:planner разбирает план.", agents, marked) == {"planner"}
    assert (
        unimplemented("Уборку делает janitor, тесты пишет tester.", agents, marked) == set()
    )  # janitor помечена


def test_the_marker_line_exists_once_and_names_only_roles_without_an_agent() -> None:
    """Пометка не устаревает: роль, у которой появился агент, из неё убирается."""
    marked = marked_roles(BACKLOG.read_text(encoding="utf-8"))
    assert marked, "в пометке нет ни одной роли"
    assert marked.isdisjoint(agent_names()), (
        f"у ролей {sorted(marked & agent_names())} уже есть агент: уберите их из пометки"
    )
    assert {
        "architect",
        "janitor",
        "explorer",
    } <= marked  # на 2026-10-07: этих агентов нет, правила на них ссылаются


@pytest.mark.parametrize("name", ["implementer", "reviewer", "tester"])
def test_every_live_role_has_a_well_formed_agent_file(name: str) -> None:
    fields = frontmatter(AGENTS / f"{name}.md")
    assert fields["name"] == name and fields["description"]
