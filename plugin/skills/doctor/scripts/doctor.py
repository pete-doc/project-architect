"""Помощник для `/parch:doctor`: временный проект, факты об окружении, проверка результата.

Вход: JSON-объект на stdin. Выход: JSON-объект на stdout.

    {"command": "diagnose"}                      факты: Python, sh, файлы плагина, запуск hooks
    {"command": "prepare"}                       временный проект с CONSTITUTION.md и три пробы
    {"command": "check", "project": "<папка>"}   что осталось после проб (сработала ли защита)
    {"command": "cleanup", "project": "<папка>"} удалить временный проект

Сами «запрещённые» действия выполняет агент в живой сессии Claude Code: так проверяется, что
Claude Code действительно запускает hooks на этом компьютере, а не только что скрипты исправны.
"""

from __future__ import annotations

import io
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
HOOKS = PLUGIN_ROOT / "hooks"
PREFIX = "parch-doctor-"
CANDIDATES = (["python3"], ["python"], ["py", "-3"])
VERSION_CHECK = "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
CONSTITUTION = """# CONSTITUTION — проверка ProjectArchitect (временный проект)

## Разрешённые пакеты

- pip: pytest

## Команды проверки

Нет: это временный проект для `/parch:doctor`.
"""


def posix(path: Path) -> str:
    return path.as_posix()


def probe_candidate(argv: list[str]) -> dict[str, Any]:
    name = " ".join(argv)
    exe = shutil.which(argv[0])
    if exe is None:
        return {"candidate": name, "found": False, "usable": False}
    try:
        version = subprocess.run(
            [*argv, "--version"], capture_output=True, text=True, timeout=20, check=False
        )
        usable = (
            subprocess.run(
                [*argv, "-c", VERSION_CHECK], capture_output=True, timeout=20, check=False
            ).returncode
            == 0
        )
        text = (version.stdout or version.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"candidate": name, "found": True, "usable": False, "error": str(error)}
    return {"candidate": name, "found": True, "path": exe, "version": text, "usable": usable}


def hook_scripts_start() -> list[dict[str, Any]]:
    """Каждый hook должен запускаться и спокойно отвечать на пустой вызов."""
    results: list[dict[str, Any]] = []
    for script in sorted(HOOKS.glob("*.py")):
        if script.name.startswith("_"):
            continue
        try:
            process = subprocess.run(
                [sys.executable, str(script)],
                input=b"{}",
                capture_output=True,
                timeout=30,
                check=False,
            )
            results.append(
                {"hook": script.name, "ok": process.returncode == 0, "code": process.returncode}
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append({"hook": script.name, "ok": False, "error": str(error)})
    return results


def diagnose() -> dict[str, Any]:
    return {
        "system": platform.platform(),
        "python_running_doctor": sys.version.split()[0],
        "python_candidates": [probe_candidate(c) for c in CANDIDATES],
        "sh_found": shutil.which("sh") is not None,
        "launcher_exists": (HOOKS / "run-hook.cmd").is_file(),
        "hooks_json_exists": (HOOKS / "hooks.json").is_file(),
        "plugin_root": posix(PLUGIN_ROOT),
        "hook_scripts": hook_scripts_start(),
    }


def prepare() -> dict[str, Any]:
    project = Path(tempfile.mkdtemp(prefix=PREFIX)).resolve()
    (project / "docs").mkdir()
    (project / "docs" / "CONSTITUTION.md").write_text(CONSTITUTION, encoding="utf-8", newline="\n")
    (project / "victim").mkdir()
    root = posix(project)
    return {
        "project": root,
        "project_native": str(project),
        "steps": [
            f'Bash: cd "{root}"  (дальше все пробы выполняются из этой папки)',
        ],
        "probes": [
            {
                "id": "destructive",
                "what": "удалить пустую временную папку командой rm -rf",
                "tool": "Bash",
                "command": f'rm -rf "{root}/victim"',
                "expect_text": "[guard_destructive]",
                "hook": "guard_destructive",
            },
            {
                "id": "markdown",
                "what": "создать новый .md-файл вне docs/",
                "tool": "Write",
                "file_path": f"{root}/notes.md",
                "content": "проверка защиты\n",
                "expect_text": "[guard_paths]",
                "hook": "guard_paths",
            },
            {
                "id": "package",
                "what": "установить пакет, которого нет в списке разрешённых",
                "tool": "Bash",
                # у pip нет пути, поэтому проект определяется переходом `cd` в той же команде:
                # папка сессии может быть пустой и без CONSTITUTION.md
                "command": f'cd "{root}" && pip install --no-index parch-doctor-probe-package',
                "expect_text": "[guard_packages]",
                "hook": "guard_packages",
            },
        ],
    }


def safe_project(raw: str) -> Path:
    path = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not path.name.startswith(PREFIX) or temp_root not in path.parents:
        raise ValueError("это не временный проект doctor: удалять отказываюсь")
    return path


def check(raw: str) -> dict[str, Any]:
    project = safe_project(raw)
    victim_exists = (project / "victim").is_dir()
    notes_exists = (project / "notes.md").exists()
    return {
        "victim_still_exists": victim_exists,
        "notes_file_created": notes_exists,
        "destructive_probe_was_blocked": victim_exists,
        "markdown_probe_was_blocked": not notes_exists,
    }


def cleanup(raw: str) -> dict[str, Any]:
    project = safe_project(raw)
    shutil.rmtree(project, ignore_errors=True)
    return {"removed": not project.exists()}


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    request: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    command = request.get("command")
    if command == "diagnose":
        result = diagnose()
    elif command == "prepare":
        result = prepare()
    elif command == "check":
        result = check(str(request.get("project", "")))
    elif command == "cleanup":
        result = cleanup(str(request.get("project", "")))
    else:
        raise ValueError("command должен быть diagnose, prepare, check или cleanup")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"doctor: {error}\n")
        sys.exit(1)
