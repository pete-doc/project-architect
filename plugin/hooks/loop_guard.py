# ruff: noqa: E501
"""PreToolUse: после петли блокирует правки кода до конца сессии (STANDARD.md, 6.3; F13).

Петля это либо три одинаковые «подписи» ошибки подряд (проверка + файл + текст ошибки; их пишут
post_edit_check и stop_gate в журнал `.claude/audit/`), либо возврат одного файла к прежнему содержимому
дважды (правка туда-обратно). Первое обнаружение записывается в журнал как флаг «loop»; с этого момента в
сессии разрешены только чтение и запись отчёта в `state/incidents/*.md`. Когда отчёт записан, правки кода
остаются запрещены: сессия завершается, продолжение новой сессией (чистый контекст). Блок определяется по
префиксу ветки (`F13-…`); без префикса петля всё равно останавливает, отчёт называет блок вручную или NONE.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, cast

from _common import (
    FILE_TOOLS,
    SHELL_TOOLS,
    Block,
    JsonDict,
    audit,
    command_name,
    command_tokens,
    get_str,
    is_managed,
    run_guard,
    shell_command,
    target_paths,
)

HOOK = "loop_guard"
SIGNATURE_LIMIT = 3  # одинаковых подписей подряд
REVERT_LIMIT = 2  # возвратов файла к прежнему содержимому
SIGNATURE = re.compile(r"sig=([0-9a-f]{8,})")
EDIT = re.compile(r"^(?P<path>.*) sha=(?P<sha>[0-9a-f]+)$")
BRANCH_BLOCK = re.compile(r"^(F\d+)(?:[-_/]|$)", re.IGNORECASE)
READ_ONLY_COMMANDS = {
    "ls", "dir", "cat", "type", "rg", "grep", "head", "tail", "wc", "pwd", "where", "which", "findstr",
    "get-content", "get-childitem", "select-string", "echo",
}  # fmt: skip
READ_ONLY_GIT = {
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "blame",
    "ls-files",
    "rev-parse",
    "grep",
}


def session_records(project: Path, session: str) -> list[dict[str, Any]]:
    """Записи журнала этой сессии по порядку (журнал: по файлу JSONL в день)."""
    records: list[dict[str, Any]] = []
    for log in sorted((project / ".claude" / "audit").glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                raw: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                record = cast("dict[str, Any]", raw)
                if record.get("session") == session:
                    records.append(record)
    return records


def signature_loop(records: list[dict[str, Any]]) -> str | None:
    """Три одинаковые подписи подряд; успешная правка («ok») прерывает ряд."""
    series: list[str | None] = []
    for record in records:
        hook, decision = record.get("hook"), record.get("decision")
        if hook == "post_edit_check" and decision == "ok":
            series.append(None)
        elif (hook, decision) in {("post_edit_check", "errors"), ("stop_gate", "block")}:
            found = SIGNATURE.search(str(record.get("detail", "")))
            series.append(found[1] if found else None)
    tail = series[-SIGNATURE_LIMIT:]
    if len(tail) == SIGNATURE_LIMIT and tail[0] is not None and len(set(tail)) == 1:
        return f"одна и та же ошибка {SIGNATURE_LIMIT} раза подряд (подпись {tail[0]})"
    return None


def revert_loop(records: list[dict[str, Any]]) -> str | None:
    """Файл дважды вернулся к прежнему содержимому: A, B, A, B."""
    states: dict[str, list[str]] = {}
    for record in records:
        if record.get("hook") == "post_edit_check" and record.get("decision") == "edit":
            found = EDIT.match(str(record.get("detail", "")))
            if found:
                states.setdefault(found["path"], []).append(found["sha"])
    for path, shas in states.items():
        reverts = sum(
            1 for i in range(2, len(shas)) if shas[i] == shas[i - 2] and shas[i] != shas[i - 1]
        )
        if reverts >= REVERT_LIMIT:
            return f"файл {Path(path).name} правят туда-обратно ({reverts} возврата)"
    return None


def loop_flag(project: Path, session: str) -> dict[str, Any] | None:
    """Запись-флаг первого обнаружения петли в этой сессии (None, если петли не было)."""
    if not session:
        return None
    for record in session_records(project, session):
        if record.get("hook") == HOOK and record.get("decision") == "loop":
            return record
    return None


def detect(project: Path, session: str) -> str | None:
    records = session_records(project, session)
    return signature_loop(records) or revert_loop(records)


def branch_block(project: Path) -> str:
    """Идентификатор блока по префиксу ветки или NONE."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project, capture_output=True, text=True, check=False, timeout=30,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "NONE"
    found = BRANCH_BLOCK.match(done.stdout.strip()) if done.returncode == 0 else None
    return found[1].upper() if found else "NONE"


def report_written_since(project: Path, stamp: str) -> bool:
    """Есть ли отчёт в state/incidents/, записанный после флага петли."""
    from datetime import datetime

    try:
        since = datetime.fromisoformat(stamp).timestamp() - 2
    except ValueError:
        return False
    folder = project / "state" / "incidents"
    if not folder.is_dir():
        return False
    return any(
        p.suffix == ".md" and p.name.lower() != "readme.md" and p.stat().st_mtime >= since
        for p in folder.iterdir()
    )


def is_report_path(path: str, project: Path) -> bool:
    try:
        relative = Path(path).resolve().relative_to(project.resolve())
    except ValueError:
        return False
    parts = PurePosixPath(relative.as_posix()).parts
    return len(parts) == 3 and parts[:2] == ("state", "incidents") and parts[2].endswith(".md")


def is_read_only(command: str) -> bool:
    """Команда только читает: чтение файлов и git status/log/diff, без перенаправлений."""
    segments = command_tokens(command)
    if not segments:
        return True
    for tokens in segments:
        if any(
            t.startswith((">", "1>", "2>")) and not t.startswith(("2>&1", ">&")) for t in tokens
        ):
            return False
        name = command_name(tokens)
        if name == "git":
            sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
            if sub not in READ_ONLY_GIT:
                return False
        elif name not in READ_ONLY_COMMANDS:
            return False
    return True


def message(project: Path, reason: str) -> str:
    block = branch_block(project)
    where = (
        f"блок {block}"
        if block != "NONE"
        else "блок не определён по ветке: назови его вручную или NONE"
    )
    return (
        f"Петля: {reason}. Дальше по кругу ходить нельзя. Правки кода в этой сессии запрещены. "
        f"Напиши отчёт state/incidents/ГГГГ-ММ-ДД-БЛОК-слово.md по шаблону docs/INCIDENT_TEMPLATE.md "
        f"({where}) и заверши сессию; продолжение только новой сессией с чистым контекстом: ей дай "
        "спецификацию, этот отчёт и результаты поиска в истории."
    )


def check(data: JsonDict, project: Path) -> Block | None:
    tool = get_str(data, "tool_name")
    if tool not in FILE_TOOLS + SHELL_TOOLS or not is_managed(project):
        return None
    session = get_str(data, "session_id")
    if not session:
        return None
    flag = loop_flag(project, session)
    reason = str(flag.get("detail", "")) if flag else detect(project, session)
    if reason is None:
        return None
    if flag is None:
        audit(project, data, HOOK, "loop", reason)
    if tool in FILE_TOOLS:
        paths = target_paths(data)
        if paths and all(is_report_path(p, project) for p in paths):
            return None
    elif is_read_only(shell_command(data)):
        return None
    return Block(HOOK, message(project, reason))


if __name__ == "__main__":
    run_guard(HOOK, check)
