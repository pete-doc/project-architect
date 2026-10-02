"""PreToolUse: охрана путей.

1. Роль implementer не правит тесты, настройки Claude, CI-файлы, state/features.json,
   CONSTITUTION.md и принятые ADR.
2. Любая роль не создаёт новые .md вне docs/ (кроме короткого списка исключений).

Активен только в проектах с CONSTITUTION.md. Правки через Bash/PowerShell ловятся
приблизительно; жёсткая защита — правила permissions (templates/claude/settings.json).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath

from _common import (
    FILE_TOOLS,
    SHELL_TOOLS,
    Block,
    JsonDict,
    agent_role,
    command_name,
    command_tokens,
    get_str,
    is_managed,
    rel_posix,
    run_guard,
    shell_command,
    target_paths,
)

HOOK = "guard_paths"

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


def _protected_reason(rel: str, project: Path) -> str | None:
    """Почему путь закрыт для implementer; None, если не закрыт."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if _is_test_path(rel):
        return "тесты правит не implementer: тест — это требование, а не деталь реализации"
    if parts[0] in (".claude", ".claude-plugin") or name == ".mcp.json":
        return "настройки Claude и hooks защищают сами проверки"
    if parts[0] in _CI_DIRS or (len(parts) == 1 and name in _CI_ROOT_FILES):
        return "CI-файлы определяют, что считается «готово»"
    if rel.endswith("state/features.json"):
        return "state/features.json пересчитывается автоматически по реальным тестам"
    if name == "constitution.md":
        return "CONSTITUTION.md (стек, список пакетов) утверждает только владелец"
    if len(parts) >= 3 and parts[-3:-1] == ("docs", "adr") and name.endswith(".md"):
        if _is_accepted_adr(rel, project):
            return "принятый ADR неизменяем: решение пересматривается новым ADR"
    return None


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


def _exists_ignoring_case(project: Path, rel: str) -> bool:
    folder = project / PurePosixPath(rel).parent
    if not folder.is_dir():
        return False
    name = PurePosixPath(rel).name
    return any(child.name.lower() == name for child in folder.iterdir())


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


def _shell_block(command: str, role: str, project: Path) -> Block | None:
    for tokens in command_tokens(command):
        for raw in _written_paths(tokens):
            rel = rel_posix(raw.strip("\"'"), project)
            if rel is None:
                continue
            if role == "implementer":
                reason = _protected_reason(rel, project)
                if reason:
                    return Block(
                        HOOK,
                        f"Команда меняет защищённый путь {rel}: {reason}. "
                        "Если правка действительно нужна, опиши её в ответе, "
                        "и это сделает другая роль или владелец.",
                    )
            block = _md_block(rel, project)
            if block:
                return block
    return None


def check(data: JsonDict, project: Path) -> Block | None:
    if not is_managed(project):
        return None
    role = agent_role(data)
    tool = get_str(data, "tool_name")
    if tool in SHELL_TOOLS:
        return _shell_block(shell_command(data), role, project)
    if tool not in FILE_TOOLS:
        return None
    for raw in target_paths(data):
        rel = rel_posix(raw, project)
        if rel is None:
            continue
        if role == "implementer":
            reason = _protected_reason(rel, project)
            if reason:
                return Block(
                    HOOK,
                    f"Роль implementer не может менять {rel}: {reason}. "
                    "Опиши нужную правку в ответе, и это сделает другая роль или владелец.",
                )
        block = _md_block(rel, project)
        if block:
            return block
    return None


if __name__ == "__main__":
    run_guard(HOOK, check)
