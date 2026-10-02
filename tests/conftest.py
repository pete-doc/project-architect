"""Общие помощники для тестов hooks: запуск hook как процесса и заготовка проекта."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "plugin" / "hooks"

CONSTITUTION = """# CONSTITUTION

## Разрешённые пакеты
- pip: requests, pydantic
- npm: react, @types/node
- nuget: Newtonsoft.Json
- psgallery: Pester

## Другое
- pip: flask
"""


class HookResult:
    def __init__(self, process: "subprocess.CompletedProcess[bytes]") -> None:
        self.code = process.returncode
        self.stdout = process.stdout.decode("utf-8", errors="replace")
        self.stderr = process.stderr.decode("utf-8", errors="replace")

    @property
    def blocked(self) -> bool:
        return self.code == 2


def run_hook(
    script: str,
    payload: dict[str, Any] | str,
    project: Path,
    role: str | None = None,
    **extra: Any,
) -> HookResult:
    """Запускает hook так же, как Claude Code: JSON на stdin, переменная CLAUDE_PROJECT_DIR."""
    body = payload if isinstance(payload, str) else ""
    if isinstance(payload, dict):
        base: dict[str, Any] = {
            "session_id": "s1",
            "cwd": str(project),
            "permission_mode": "default",
        }
        data: dict[str, Any] = {**base, **payload, **extra}
        if role:
            data["agent_type"] = role
        if data["permission_mode"] is None:
            del data["permission_mode"]
        body = json.dumps(data)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "PYTHONIOENCODING": "utf-8"}
    process = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=body.encode("utf-8"),
        capture_output=True,
        env=env,
        check=False,
        timeout=180,
    )
    return HookResult(process)


def bash(command: str, event: str = "PreToolUse") -> dict[str, Any]:
    return {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command}}


def powershell(command: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "PowerShell",
        "tool_input": {"command": command},
    }


def file_call(tool: str, path: Path | str, event: str = "PreToolUse") -> dict[str, Any]:
    return {"hook_event_name": event, "tool_name": tool, "tool_input": {"file_path": str(path)}}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Проект, подключённый к ProjectArchitect: есть CONSTITUTION.md, принятый и новый ADR."""
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "CONSTITUTION.md").write_text(CONSTITUTION, encoding="utf-8")
    (tmp_path / "docs" / "adr" / "0001-accepted.md").write_text(
        "# ADR-0001\n\n- Статус: accepted\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "adr" / "0002-proposed.md").write_text(
        "# ADR-0002\n\n- Статус: proposed\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def bare_project(tmp_path: Path) -> Path:
    """Проект без CONSTITUTION.md: ProjectArchitect к нему не подключён."""
    return tmp_path
