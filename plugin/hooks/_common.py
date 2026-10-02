"""Общие функции hooks ProjectArchitect. Только стандартная библиотека.

Протокол Claude Code: hook получает JSON на stdin; код выхода 2 блокирует действие,
а текст из stderr показывается агенту. Любой другой код (в том числе 1) НЕ блокирует,
поэтому охранные hooks при любой собственной ошибке сами завершаются кодом 2.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

JsonDict = dict[str, Any]

CONSTITUTION_CANDIDATES = ("docs/CONSTITUTION.md", "CONSTITUTION.md")
SHELL_TOOLS = ("Bash", "PowerShell")
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


@dataclass(frozen=True)
class Block:
    """Решение «остановить»: reason показывается агенту, поэтому пишется по-русски и по делу."""

    hook: str
    reason: str


def _use_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def read_input() -> JsonDict:
    _use_utf8()
    raw = sys.stdin.read()
    data: object = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("hook получил не JSON-объект")
    return {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def get_str(data: JsonDict, key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def tool_input(data: JsonDict) -> JsonDict:
    value = data.get("tool_input")
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return {}


def project_dir(data: JsonDict) -> Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or get_str(data, "cwd") or os.getcwd()
    return Path(base).resolve()


def constitution_path(project: Path) -> Path | None:
    for candidate in CONSTITUTION_CANDIDATES:
        path = project / candidate
        if path.is_file():
            return path
    return None


def is_managed(project: Path) -> bool:
    """Проект подключён к ProjectArchitect, если в нём есть CONSTITUTION.md."""
    return constitution_path(project) is not None


def agent_role(data: JsonDict) -> str:
    """Роль агента: последняя часть agent_type (`parch:implementer` -> `implementer`).

    Для основной сессии без --agent поле отсутствует, роль пустая.
    """
    return get_str(data, "agent_type").rsplit(":", 1)[-1].strip().lower()


def rel_posix(file_path: str, project: Path) -> str | None:
    """Путь относительно проекта с прямыми слэшами в нижнем регистре; None, если он вне проекта.

    Регистр сводится к нижнему, потому что файловые системы Windows и macOS регистр не различают.
    """
    if not file_path:
        return None
    candidate = Path(file_path.replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        relative = candidate.resolve().relative_to(project)
    except ValueError:
        return None
    return PurePosixPath(*relative.parts).as_posix().lower()


# ---------- разбор shell-команд (best effort) ----------

_SEPARATORS = ("&&", "||", ";", "|", "\n", "(", ")", "`")
_WRAPPER_SHELLS = {"bash", "sh", "zsh", "dash", "pwsh", "powershell", "cmd"}
_WRAPPER_FLAGS = {"-c", "-command", "-commandwithargs", "/c", "/k"}
_PREFIXES = {"sudo", "command", "exec", "time", "nohup", "env", "nice", "call", "&"}


def _split_segments(command: str) -> list[str]:
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        sep = next((s for s in _SEPARATORS if command.startswith(s, i)), None)
        if sep is not None:
            segments.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s for s in segments if s.strip()]


def _tokenize(segment: str) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(\S+)', segment):
        quoted_double, quoted_single, bare = match.groups()
        tokens.append(quoted_double or quoted_single or bare or "")
    return tokens


def _strip_prefixes(tokens: list[str]) -> list[str]:
    while tokens and (tokens[0].lower() in _PREFIXES or re.match(r"^\w+=", tokens[0])):
        tokens = tokens[1:]
    return tokens


def command_tokens(command: str, _depth: int = 0) -> list[list[str]]:
    """Делит команду на подкоманды и токены; раскрывает `bash -c "..."` и `powershell -Command`."""
    result: list[list[str]] = []
    for segment in _split_segments(command):
        tokens = _strip_prefixes(_tokenize(segment))
        if not tokens:
            continue
        result.append(tokens)
        name = PurePosixPath(tokens[0].replace("\\", "/")).name.lower().removesuffix(".exe")
        if name in _WRAPPER_SHELLS and _depth < 3:
            for index, token in enumerate(tokens[1:], start=1):
                if token.lower() in _WRAPPER_FLAGS and index + 1 < len(tokens):
                    result.extend(command_tokens(" ".join(tokens[index + 1 :]), _depth + 1))
                    break
    return result


def command_name(tokens: list[str]) -> str:
    if not tokens:
        return ""
    return PurePosixPath(tokens[0].replace("\\", "/")).name.lower().removesuffix(".exe")


def shell_command(data: JsonDict) -> str:
    if get_str(data, "tool_name") not in SHELL_TOOLS:
        return ""
    command = tool_input(data).get("command")
    return command if isinstance(command, str) else ""


def target_paths(data: JsonDict) -> list[str]:
    """Пути, которые затрагивает вызов файлового инструмента."""
    if get_str(data, "tool_name") not in (*FILE_TOOLS, "Read"):
        return []
    path = tool_input(data).get("file_path")
    if not isinstance(path, str):
        path = tool_input(data).get("notebook_path")
    return [path] if isinstance(path, str) and path else []


# ---------- журнал ----------

_SECRET_VALUE = re.compile(
    r"(?i)((?:token|password|passwd|secret|api[_-]?key|authorization)[\"'=:\s]+"
    r"(?:(?:bearer|token|basic)\s+)?)(\S+)"
)


def redact(text: str) -> str:
    return _SECRET_VALUE.sub(r"\1***", text)


def audit(project: Path, data: JsonDict, hook: str, decision: str, detail: str = "") -> None:
    """Дописывает строку в `.claude/audit/ГГГГ-ММ-ДД.jsonl`. Ошибки журнала не мешают работе."""
    try:
        if not is_managed(project):
            return
        now = datetime.now(UTC)
        folder = project / ".claude" / "audit"
        folder.mkdir(parents=True, exist_ok=True)
        record: JsonDict = {
            "ts": now.isoformat(timespec="seconds"),
            "session": get_str(data, "session_id"),
            "agent": get_str(data, "agent_type"),
            "event": get_str(data, "hook_event_name"),
            "tool": get_str(data, "tool_name"),
            "hook": hook,
            "decision": decision,
            "detail": redact(detail)[:500],
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with (folder / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        return


def describe_call(data: JsonDict) -> str:
    command = shell_command(data)
    if command:
        return command
    paths = target_paths(data)
    return ", ".join(paths)


# ---------- запуск охранного hook ----------

Check = Callable[[JsonDict, Path], Block | None]


def run_guard(hook: str, check: Check) -> NoReturn:
    """Читает вход, вызывает check; при блоке пишет причину в stderr и завершает кодом 2.

    Любая внутренняя ошибка тоже блокирует: упавший охранник не должен молча пропускать действие.
    """
    try:
        data = read_input()
        project = project_dir(data)
        block = check(data, project)
        if block is None:
            sys.exit(0)
        audit(project, data, hook, "block", f"{describe_call(data)} :: {block.reason}")
        sys.stderr.write(f"[{hook}] {block.reason}\n")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception as error:  # намеренно ловим всё: упавший охранник должен блокировать
        sys.stderr.write(
            f"[{hook}] Проверка безопасности сломалась ({type(error).__name__}: {error}). "
            "Действие остановлено на всякий случай. Сообщи владельцу: нужен ремонт hook.\n"
        )
        sys.exit(2)
