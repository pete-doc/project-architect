"""PreToolUse: охрана путей. Правила одинаковы для всех, включая основную сессию без роли.

1. Тесты: правят только роли architect и tester (список TEST_ROLES). Остальным блок.
2. Защищённые пути (настройки Claude и hooks, CI-файлы, файлы state/ проверок, CONSTITUTION.md,
   принятые ADR): блокировка для всех. Снять её может только владелец, подтвердив запрос,
   который Claude Code показывает ему (решение «ask»). Агент подтвердить запрос не может.
   Когда запрос показать некому (режимы bypassPermissions и dontAsk, фоновый запуск), действие
   запрещается совсем.
3. Любая роль не создаёт новые .md вне docs/ (кроме короткого списка исключений).

Активен только в проектах с CONSTITUTION.md. Правки через Bash/PowerShell ловятся
приблизительно; жёсткая защита: правила permissions (plugin/plugin/templates/claude/settings.json).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath

from _common import (
    FILE_TOOLS,
    SHELL_TOOLS,
    Ask,
    Block,
    JsonDict,
    agent_role,
    command_name,
    command_tokens,
    get_str,
    is_managed,
    rel_posix,
    roots_for_path,
    run_guard,
    shell_command,
    target_paths,
    tool_input,
)

HOOK = "guard_paths"
TEST_ROLES = frozenset({"architect", "tester"})

_TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
_TEST_NAME_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.*",
    "*.spec.*",
    "*test.cs",
    "*tests.cs",
    "*.tests.ps1",
)
_CI_ROOT_FILES = {
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "jenkinsfile",
    ".pre-commit-config.yaml",
}
_CI_DIRS = {".github", ".circleci"}
_STATE_FILES = {"features.json", "baseline.json", "jscpd-baseline.json", "vulture-whitelist.py"}
_ALLOWED_ROOT_MD = {"agents.md", "claude.md", "readme.md"}
_ADR_STATUS = re.compile(r"(?im)^\s*[-*]?\s*(?:статус|status)\s*:\s*(accepted|принят|утвержд)")

_MUTATING_COMMANDS = {
    "rm",
    "mv",
    "touch",
    "tee",
    "truncate",
    "del",
    "erase",
    "ri",
    "remove-item",
    "move",
    "move-item",
    "mi",
    "rename-item",
    "ren",
    "set-content",
    "add-content",
    "out-file",
    "clear-content",
    "new-item",
    "ni",
    "sed",
    "perl",
}
_COPY_COMMANDS = {"cp", "copy", "copy-item", "cpi", "xcopy"}


def _is_test_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    dirs = parts[:-1]
    if any(d in _TEST_DIRS or d.endswith((".tests", ".test")) or d.endswith("tests") for d in dirs):
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in _TEST_NAME_PATTERNS)


def _is_accepted_adr(rel: str, project: Path) -> bool:
    # Имя в нижнем регистре могло не совпасть с реальным; ищем файл без учёта регистра.
    folder = project / PurePosixPath(rel).parent
    if not folder.is_dir():
        return False
    for candidate in folder.iterdir():
        if candidate.name.lower() == PurePosixPath(rel).name and candidate.is_file():
            head = candidate.read_text(encoding="utf-8", errors="replace")[:2000]
            return bool(_ADR_STATUS.search(head))
    return False


def _gated_reason(rel: str, project: Path) -> str | None:
    """Почему путь закрыт для всех, пока владелец не подтвердит; None, если не закрыт."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if parts[0] in (".claude", ".claude-plugin") or name == ".mcp.json":
        return "это настройки Claude и hooks: ими держатся все остальные проверки"
    if parts[0] in _CI_DIRS or (len(parts) == 1 and name in _CI_ROOT_FILES):
        return "это CI-файл: он определяет, что считается «готово»"
    if len(parts) >= 2 and parts[-2] == "state" and name in _STATE_FILES:
        return (
            f"state/{name} относится к проверкам CI («храповик»): его нельзя менять, "
            "чтобы пропустить нарушение"
        )
    if name == "constitution.md":
        return "CONSTITUTION.md (стек, список пакетов) утверждает только владелец"
    if len(parts) >= 3 and parts[-3:-1] == ("docs", "adr") and name.endswith(".md"):
        if _is_accepted_adr(rel, project):
            return "принятый ADR неизменяем: решение пересматривается новым ADR"
    return None


_ACCEPTED_WORD = re.compile(r"(?i)\b(accepted|принят\w*|утвержд\w*)\b")


def _strings(value: object) -> list[str]:
    """Все строки внутри значения (для поиска статуса в содержимом правки)."""
    if isinstance(value, str):
        return [value]
    items: list[object] = []
    if isinstance(value, dict):
        items = list(value.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    elif isinstance(value, list):
        items = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return [text for item in items for text in _strings(item)]


def _is_adr(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return len(parts) >= 3 and parts[-3:-1] == ("docs", "adr") and parts[-1].endswith(".md")


def _sets_accepted_status(data: JsonDict, command: str) -> bool:
    """Правка пытается перевести ADR в accepted или создать его уже принятым."""
    fields = tool_input(data)
    texts = [command] if command else _strings(fields)
    if any(_ADR_STATUS.search(text) for text in texts):
        return True
    old = " ".join(_strings(fields.get("old_string")))
    new = " ".join(_strings(fields.get("new_string")))
    return "proposed" in (old or command).lower() and bool(_ACCEPTED_WORD.search(new or command))


def _decide(rel: str, role: str, project: Path, data: JsonDict, command: str) -> Block | Ask | None:
    if _is_test_path(rel) and role not in TEST_ROLES:
        shown = f"«{role}»" if role else "основная сессия без роли"
        return Block(
            HOOK,
            f"Тесты ({rel}) правят только роли architect и tester, а у тебя {shown}. "
            "Тест — это требование, а не деталь реализации. Опиши нужную правку в ответе: "
            "её сделает architect или tester.",
        )
    gated = _gated_reason(rel, project)
    if gated is None and _is_adr(rel) and _sets_accepted_status(data, command):
        gated = "статус ADR accepted ставит только владелец: агент оставляет proposed"
    if gated:
        return Ask(
            HOOK,
            f"Нужно подтверждение владельца: изменяется защищённый путь {rel}, {gated}. "
            "Подтверждай, только если сам просил именно эту правку.",
        )
    return None


def _md_creation_allowed(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if "docs" in parts[:-1]:
        return True
    if len(parts) == 1 and name in _ALLOWED_ROOT_MD:
        return True
    if name == "skill.md" and "skills" in parts[:-1]:
        return True
    if "agents" in parts[:-1] and name.endswith(".md"):
        return True
    return len(parts) >= 2 and parts[0] in {"analysis", "state"}


def _exists_ignoring_case(project: Path, rel: str) -> bool:
    folder = project / PurePosixPath(rel).parent
    if not folder.is_dir():
        return False
    name = PurePosixPath(rel).name
    return any(child.name.lower() == name for child in folder.iterdir())


def _md_block(rel: str, project: Path) -> Block | None:
    if not rel.endswith(".md") or _md_creation_allowed(rel):
        return None
    if _exists_ignoring_case(project, rel):
        return None
    return Block(
        HOOK,
        f"Нельзя создавать новые .md-файлы вне docs/: {rel}. Рабочие заметки и отчёты в "
        "репозиторий не кладём. Решения оформляй как ADR в docs/adr/, остальное напиши "
        "в ответе, а не в файле.",
    )


def _written_paths(tokens: list[str]) -> list[str]:
    """Пути, которые команда (по грубой оценке) меняет, создаёт или удаляет."""
    name = command_name(tokens)
    paths: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>", "1>", "2>", "&>"} and index + 1 < len(tokens):
            paths.append(tokens[index + 1])
            skip_next = True
        elif token.startswith(">") and len(token) > 1:
            paths.append(token.lstrip(">"))
    positional = [t for t in tokens[1:] if not t.startswith(("-", ">"))]
    if name in _MUTATING_COMMANDS:
        if name == "sed" and not any(t.startswith("-i") or t == "--in-place" for t in tokens):
            return paths
        if name == "perl" and not any(t.startswith("-i") for t in tokens):
            return paths
        paths.extend(positional)
    elif name in _COPY_COMMANDS and positional:
        paths.append(positional[-1])
    elif name == "git" and any(t in tokens for t in ("checkout", "restore", "rm", "mv")):
        paths.extend(positional)
    return paths


def _check_paths(
    raws: list[str], role: str, session_project: Path, data: JsonDict, command: str
) -> Block | Ask | None:
    asked: Ask | None = None
    cwd = Path(get_str(data, "cwd") or session_project)
    for raw in raws:
        cleaned = raw.strip("\"'").replace("\\", "/")
        if not Path(cleaned).is_absolute():
            cleaned = str(cwd / cleaned)  # относительный путь считается от текущей папки вызова
        for root in roots_for_path(cleaned, session_project):
            if not is_managed(root):
                continue
            rel = rel_posix(cleaned, root)
            if rel is None:
                continue
            decision = _decide(rel, role, root, data, command)
            if isinstance(decision, Block):
                return decision
            if decision is not None and asked is None:
                asked = decision
            block = _md_block(rel, root)
            if block:
                return block
    return asked


def check(data: JsonDict, project: Path) -> Block | Ask | None:
    role = agent_role(data)
    tool = get_str(data, "tool_name")
    command = ""
    if tool in SHELL_TOOLS:
        command = shell_command(data)
        raws = [raw for tokens in command_tokens(command) for raw in _written_paths(tokens)]
    elif tool in FILE_TOOLS:
        raws = target_paths(data)
    else:
        return None
    return _check_paths(raws, role, project, data, command)


if __name__ == "__main__":
    run_guard(HOOK, check)
