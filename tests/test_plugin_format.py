# ruff: noqa: E501
"""F16, PR 1: формат навыков и ролей плагина по правилам Anthropic.

Правила (platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): name до 64 символов, только строчные
латинские буквы, цифры и дефисы, без слов claude и anthropic; description не пустое, до 1024 символов, без XML-тегов; тело
SKILL.md до 500 строк; пути только с прямым слэшем; файлы навыка подключаются ссылкой прямо из SKILL.md, вложенный файл не
ссылается дальше. Роль (agents/*.md) по нашему правилу не длиннее 40 строк.

Плохие примеры, которые проверка обязана остановить: имя из 70 символов, с заглавной буквой, со словом claude; пустое
описание, описание в 1100 символов, с XML-тегом; тело в 600 строк; путь с обратным слэшем; ссылка через вложенный файл и
ссылка за пределы навыка; роль в 41 строку.
"""

import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
SKILLS = sorted(PLUGIN.glob("skills/*/SKILL.md"))
AGENTS = sorted(PLUGIN.glob("agents/*.md"))

NAME = re.compile(r"[a-z0-9-]+")
RESERVED = ("claude", "anthropic")
XML_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>")
# путь с обратным слэшем: папка\файл.расширение (`scripts\x.py`, `C:\dev\a.md`); экранирование в регулярных выражениях
# (`\b`, `\n`, `\.py`) под это не подходит
BACKSLASH_PATH = re.compile(r"[\w.-]+\\(?:[\w.-]+\\)*[\w-]+\.[A-Za-z0-9]{1,5}\b")
LOCAL_LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)\s]+)\)")
MULTILINE_YAML = {">", "|", ">-", "|-", ">+", "|+"}


def split(text: str) -> tuple[dict[str, str], str]:
    """Поля frontmatter (строка «ключ: значение») и тело; без frontmatter поля пустые."""
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text.removeprefix("---\n").partition("\n---\n")
    if not sep:
        return {}, text
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        if key.strip() and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("\"'")
    return fields, body


def frontmatter_problems(fields: dict[str, str]) -> list[str]:
    problems: list[str] = []
    name = fields.get("name", "")
    if len(name) > 64:
        problems.append(f"name длиннее 64 символов ({len(name)})")
    if not NAME.fullmatch(name):
        problems.append(f"name не только из строчных букв, цифр и дефисов: {name[:70]!r}")
    if any(word in name.lower() for word in RESERVED):
        problems.append("name со словом claude или anthropic")
    description = fields.get("description", "")
    if not description:
        problems.append("description пустое")
    if description in MULTILINE_YAML:
        problems.append("description многострочное: проверка его не читает, пиши одной строкой")
    if len(description) > 1024:
        problems.append(f"description длиннее 1024 символов ({len(description)})")
    if XML_TAG.search(description):
        problems.append("description с XML-тегом")
    return problems


def path_problems(text: str) -> list[str]:
    return [f"путь с обратным слэшем: {p}" for p in BACKSLASH_PATH.findall(text)]


def link_problems(skill_md: Path) -> list[str]:
    """Ссылки из SKILL.md ведут на файлы внутри навыка; другие .md навыка не ссылаются дальше."""
    problems: list[str] = []
    skill_dir = skill_md.parent.resolve()
    for target in LOCAL_LINK.findall(skill_md.read_text(encoding="utf-8")):
        path = (skill_dir / target.split("#")[0]).resolve()
        if not path.is_relative_to(skill_dir) or not path.is_file():
            problems.append(f"ссылка вне навыка или на несуществующий файл: {target}")
    for other in sorted(skill_dir.rglob("*.md")):
        if other.name != "SKILL.md" and LOCAL_LINK.search(other.read_text(encoding="utf-8")):
            problems.append(f"вложенная ссылка: {other.name} ссылается дальше, а не SKILL.md")
    return problems


def skill_problems(skill_md: Path) -> list[str]:
    text = skill_md.read_text(encoding="utf-8")
    fields, body = split(text)
    if not fields:
        return ["нет frontmatter"]
    problems = frontmatter_problems(fields)
    lines = len(body.splitlines())
    if lines > 500:
        problems.append(f"тело SKILL.md длиннее 500 строк ({lines})")
    return problems + path_problems(text) + link_problems(skill_md)


def agent_problems(text: str) -> list[str]:
    fields, _ = split(text)
    if not fields:
        return ["нет frontmatter"]
    problems = frontmatter_problems(fields)
    lines = len(text.splitlines())
    if lines > 40:
        problems.append(f"роль длиннее 40 строк ({lines})")
    return problems + path_problems(text)


@pytest.mark.parametrize("skill_md", SKILLS, ids=[p.parent.name for p in SKILLS])
def test_every_skill_of_the_plugin_follows_the_format(skill_md: Path) -> None:
    assert skill_problems(skill_md) == []


@pytest.mark.parametrize("agent", AGENTS, ids=[p.stem for p in AGENTS])
def test_every_role_of_the_plugin_follows_the_format(agent: Path) -> None:
    assert agent_problems(agent.read_text(encoding="utf-8")) == []


# ---------- плохие примеры: копия настоящего навыка review с одной поломкой ----------


@pytest.fixture
def skill(tmp_path: Path) -> Path:
    copy = tmp_path / "skills" / "review"
    shutil.copytree(PLUGIN / "skills" / "review", copy)
    return copy


def set_field(field: str, value: str) -> Callable[[Path], None]:
    def mutate(skill: Path) -> None:
        md = skill / "SKILL.md"
        text = md.read_text(encoding="utf-8")
        changed = re.sub(rf"(?m)^{field}:.*$", lambda _: f"{field}: {value}", text, count=1)
        assert changed != text
        md.write_text(changed, encoding="utf-8")

    return mutate


def append(extra: str) -> Callable[[Path], None]:
    def mutate(skill: Path) -> None:
        md = skill / "SKILL.md"
        md.write_text(md.read_text(encoding="utf-8") + extra, encoding="utf-8")

    return mutate


def nested_reference(skill: Path) -> None:
    (skill / "reference.md").write_text("Подробности: [дальше](details.md)\n", encoding="utf-8")
    (skill / "details.md").write_text("Настоящие сведения.\n", encoding="utf-8")
    append("\nСправка: [reference.md](reference.md)\n")(skill)


def test_an_untouched_copy_of_a_good_skill_passes(skill: Path) -> None:
    # поломки ниже ловятся из-за самой поломки, а не из-за копирования
    assert skill_problems(skill / "SKILL.md") == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(set_field("name", "a" * 70), "name длиннее 64", id="name-70"),
        pytest.param(set_field("name", "Review"), "строчных букв", id="name-uppercase"),
        pytest.param(set_field("name", "claude-review"), "claude или anthropic", id="name-claude"),
        pytest.param(set_field("description", ""), "description пустое", id="description-empty"),
        pytest.param(set_field("description", "о" * 1100), "длиннее 1024", id="description-1100"),
        pytest.param(
            set_field("description", "Ревью <b>PR</b>"), "XML-тегом", id="description-xml"
        ),
        pytest.param(append("строка\n" * 600), "тело SKILL.md длиннее 500", id="body-600"),
        pytest.param(
            append("Запусти `python scripts\\review.py`.\n"), "обратным слэшем", id="backslash"
        ),
        pytest.param(nested_reference, "вложенная ссылка", id="nested-reference"),
        pytest.param(append("[чужой](../adr/SKILL.md)\n"), "вне навыка", id="link-outside"),
    ],
)
def test_a_skill_that_breaks_a_format_rule_is_rejected(
    skill: Path, mutate: Callable[[Path], None], expected: str
) -> None:
    mutate(skill)
    problems = skill_problems(skill / "SKILL.md")
    assert any(expected in p for p in problems), problems


def test_a_role_longer_than_40_lines_is_rejected() -> None:
    text = (PLUGIN / "agents" / "reviewer.md").read_text(encoding="utf-8")
    padded = text + "строка\n" * (41 - len(text.splitlines()))
    assert len(padded.splitlines()) == 41
    assert any("роль длиннее 40 строк" in p for p in agent_problems(padded))


def test_a_role_with_a_bad_name_is_rejected() -> None:
    text = (PLUGIN / "agents" / "reviewer.md").read_text(encoding="utf-8")
    bad = text.replace("name: reviewer", "name: Claude-Reviewer")
    problems = agent_problems(bad)
    assert any("строчных букв" in p for p in problems) and any("claude" in p for p in problems)
