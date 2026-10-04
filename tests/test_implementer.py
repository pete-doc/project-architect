# ruff: noqa: E501
"""F15, PR 4: роль «исполнитель» с протоколом из 5 шагов перед новой функцией.

Плохие примеры: у файла роли нет обязательных полей (Claude Code молча пропускает такого агента), в протоколе не
хватает шага или нет раздела «Основания».
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "plugin" / "agents" / "implementer.md"


def frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n"), "первая строка файла роли должна быть ---"
    head = text.split("\n---\n", 1)[0].removeprefix("---\n")
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in head.splitlines())}


def test_the_role_file_has_the_fields_claude_code_requires() -> None:
    fields = frontmatter(AGENT.read_text(encoding="utf-8"))
    name = fields["name"]
    assert name == "implementer" and ":" not in name and not name.startswith("-")
    assert fields["description"], "без description агента Claude Code пропускает молча"
    assert set(fields) <= {"name", "description", "tools", "model"}
    for ignored in ("hooks", "mcpServers", "permissionMode"):
        assert ignored not in fields  # плагин-агенты эти поля игнорируют


def test_the_protocol_has_five_steps_in_the_order_the_spec_requires() -> None:
    text = AGENT.read_text(encoding="utf-8")
    steps = re.findall(r"^(\d)\. \*\*(.+?)\.\*\*", text.split("## Основания")[0], re.MULTILINE)
    assert [n for n, _ in steps] == ["1", "2", "3", "4", "5"]
    titles = " ".join(t for _, t in steps).lower()
    for part in ("каталог", "реестр", "поиск по коду", "история git", "уроки", "решение"):
        assert part in titles
    body = text.lower()
    for source in ("capabilities.md", "modules.md", "git log -s", "lessons.md", "state/incidents"):
        assert source in body


def test_the_role_file_is_short() -> None:
    lines = AGENT.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 40, f"implementer.md: {len(lines)} строк, предел 40 (решение владельца)"


def test_the_role_name_and_tools_agree_with_what_guard_paths_checks() -> None:
    """Роль не из тех, кому охрана путей разрешает править тесты; её инструменты записи под охраной."""
    import json

    import guard_paths

    fields = frontmatter(AGENT.read_text(encoding="utf-8"))
    role = fields["name"]
    assert role not in guard_paths.TEST_ROLES  # тесты правят только architect и tester
    text = AGENT.read_text(encoding="utf-8")
    for test_role in guard_paths.TEST_ROLES:
        assert test_role in text  # роль знает, к кому идти за тестом
    hooks = json.loads((ROOT / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    guarded: set[str] = set()
    for entry in hooks["hooks"]["PreToolUse"]:
        if any("guard_paths.py" in h["command"] for h in entry["hooks"]):
            guarded = set(entry["matcher"].split("|"))
    tools = {t.strip() for t in fields["tools"].split(",")}
    writing = tools & {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell"}
    assert (
        writing and writing <= guarded
    )  # каждый инструмент записи роли проходит через guard_paths
    # agent_role берёт последнюю часть agent_type: «parch:implementer» -> «implementer»
    from _common import agent_role

    assert agent_role({"agent_type": f"parch:{role}"}) == role


def test_the_role_requires_the_grounds_section_and_forbids_going_around_blocks() -> None:
    text = AGENT.read_text(encoding="utf-8")
    assert "Основания" in text and "не обходи" in text
