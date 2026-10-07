"""Stop: не даёт агенту закончить, пока не проходят тесты и проверки проекта.

Команды берутся из раздела «Команды проверки» в CONSTITUTION.md (по команде на строку):

    ## Команды проверки
    - pytest -q
    - ruff check .

Если раздела нет, для Python-проекта используются pytest, ruff и pyright, когда они настроены.
Защита от зацикливания: если Claude Code уже продолжает работу из-за этого hook
(`stop_hook_active`) и ошибки те же, что в прошлый раз, остановка разрешается с предупреждением;
если ошибки изменились (прогресс есть), агент получает ещё один шанс. Сверх того Claude Code сам
прерывает цепочку после 8 блокировок подряд.
Проверка запускается, только если в сессии что-то правили или в git есть несохранённые изменения.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from _common import (
    JsonDict,
    audit,
    command_tokens,
    constitution_path,
    get_str,
    is_managed,
    project_dir,
    read_input,
)
from loop_guard import loop_flag, report_written_since

HOOK = "stop_gate"
COMMAND_TIMEOUT = 600
PYTHON_TOOLS = frozenset({"pytest", "ruff", "pyright"})  # как в CI; без PATH запускаются через -m
TAIL_LINES = 40
_HEADING = re.compile(r"^#{1,6}\s*(команды проверки|check commands)\s*$", re.IGNORECASE)
_ANY_HEADING = re.compile(r"^#{1,6}\s")


def configured_commands(constitution: Path) -> list[str]:
    commands: list[str] = []
    inside = False
    for line in constitution.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if _HEADING.match(stripped):
            inside = True
        elif inside and _ANY_HEADING.match(stripped):
            break
        elif inside and stripped.startswith(("-", "*")):
            command = stripped.lstrip("-* ").strip().strip("`")
            if command:
                commands.append(command)
    return commands


def detected_commands(project: Path) -> list[str]:
    commands: list[str] = []
    pyproject = project / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8", errors="replace") if pyproject.is_file() else ""
    has_python = pyproject.is_file() or (project / "pytest.ini").is_file()
    if has_python and ((project / "tests").is_dir() or "[tool.pytest" in text):
        commands.append("pytest -q")  # как в CI: `python -m pytest` скрывает ошибку импорта
    if "[tool.ruff" in text or (project / "ruff.toml").is_file():
        commands.append("ruff check .")
    if "[tool.pyright" in text or (project / "pyrightconfig.json").is_file():
        commands.append("pyright")
    return commands


def _git(project: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=project, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def session_has_changes(project: Path, session_id: str) -> bool:
    status = _git(project, "status", "--porcelain")
    if status is None or status.strip():
        return True  # не git-репозиторий или есть несохранённые изменения
    for log in sorted((project / ".claude" / "audit").glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record: object = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            fields = cast("dict[str, object]", record)
            if fields.get("session") == session_id and fields.get("hook") == "post_edit_check":
                return True
    return False


def run_checks(project: Path, commands: list[str]) -> list[str]:
    """Возвращает описания упавших проверок (пусто, если все прошли)."""
    failures: list[str] = []
    for command in commands:
        tokens = command_tokens(command)
        if not tokens:
            continue
        argv = tokens[0]
        found = shutil.which(argv[0])  # npm, npx и другие .cmd на Windows находятся только так
        if found:
            argv = [found, *argv[1:]]
        elif argv[0] in PYTHON_TOOLS:  # нет в PATH (pip --user, venv не активирован): не блокируем
            argv = [sys.executable, "-m", *argv]
        try:
            result = subprocess.run(
                argv,
                cwd=project,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=COMMAND_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(f"$ {command}\nне удалось выполнить: {type(error).__name__}: {error}")
            continue
        if result.returncode != 0:
            output = (result.stdout + "\n" + result.stderr).strip().splitlines()
            failures.append(
                f"$ {command} (код {result.returncode})\n" + "\n".join(output[-TAIL_LINES:])
            )
    return failures


def _state_file(project: Path) -> Path:
    return project / ".claude" / "audit" / "stop_gate_state.json"


def _load_state(project: Path) -> dict[str, str]:
    try:
        raw: object = json.loads(_state_file(project).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def _save_state(project: Path, state: dict[str, str]) -> None:
    try:
        path = _state_file(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        return


def decide(data: JsonDict, project: Path) -> str | None:
    """Сообщение для агента, если остановку нужно запретить; None, если можно останавливаться."""
    constitution = constitution_path(project)
    if constitution is None:
        return None
    commands = configured_commands(constitution) or detected_commands(project)
    session = get_str(data, "session_id")
    flag = loop_flag(project, session)
    if flag is not None:  # петля: сессия заканчивается отчётом, а не новой попыткой (F13)
        if report_written_since(project, str(flag.get("ts", ""))):
            warning = (
                "loop_guard: в сессии была петля, отчёт записан. Правки кода запрещены, сессия "
                "завершается; продолжение только новой сессией."
            )
            sys.stdout.write(json.dumps({"systemMessage": warning}, ensure_ascii=False) + chr(10))
            return None
        audit(project, data, HOOK, "block", "петля: отчёт об инциденте ещё не записан")
        return (
            "Была петля ("
            + str(flag.get("detail", ""))
            + "). Закончить можно только после отчёта: "
            "напиши state/incidents/ГГГГ-ММ-ДД-БЛОК-слово.md по шаблону docs/INCIDENT_TEMPLATE.md "
            "(блок по ветке или NONE). Больше ничего не правь."
        )
    if not commands or not session_has_changes(project, session):
        return None
    failures = run_checks(project, commands)
    state = _load_state(project)
    if not failures:
        state.pop(session, None)
        _save_state(project, state)
        return None
    report = "\n\n".join(failures)
    fingerprint = hashlib.sha256(report.encode("utf-8")).hexdigest()
    repeated = bool(data.get("stop_hook_active")) and state.get(session) == fingerprint
    state[session] = fingerprint
    _save_state(project, state)
    if repeated:
        audit(project, data, HOOK, "allow-stop", "те же ошибки после повторной попытки")
        warning = (
            "stop_gate: проверки по-прежнему не проходят, и ошибки не изменились. "
            "Остановка разрешена, чтобы не зациклиться. Работа не закончена."
        )
        sys.stdout.write(json.dumps({"systemMessage": warning}, ensure_ascii=False) + "\n")
        return None
    audit(
        project,
        data,
        HOOK,
        "block",
        f"{len(failures)} проверок не прошли; sig={fingerprint[:12]}",
    )
    return (
        "Закончить нельзя: тесты или проверки не проходят. Исправь код (не тесты и не проверки) "
        "и повтори.\n\n" + report
    )


def main() -> None:
    try:
        data = read_input()
        project = project_dir(data)
        if not is_managed(project):
            sys.exit(0)
        message = decide(data, project)
        if message is None:
            sys.exit(0)
        sys.stderr.write(message + "\n")
        sys.exit(2)
    except SystemExit:
        raise
    except Exception as error:
        # Сломанный gate не должен навсегда запирать сессию: сообщаем и пропускаем.
        sys.stderr.write(f"[{HOOK}] gate не отработал: {type(error).__name__}: {error}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
