"""run-hook.cmd: запускающий файл сам находит Python и не зависит от способа его установки.

Установку Python подменяют «заглушки» python3, python и py в начале PATH: рабочая, сломанная
(как заглушка Microsoft Store, код 9009), слишком старая. Два пути запуска: sh (macOS, Linux,
Git Bash) и cmd (Windows без Git Bash); каждый проверяется там, где он существует.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from conftest import HOOKS, HookResult

LAUNCHER = HOOKS / "run-hook.cmd"
REAL_PYTHON = Path(sys.executable)
BAD_COMMAND = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf build"},
    "session_id": "s1",
}
GOOD_COMMAND = {**BAD_COMMAND, "tool_input": {"command": "ls"}}
NOT_FOUND = {"sh": "Не найден Python 3.12 или новее", "cmd": "Python 3.12 or newer not found"}

RUNNERS = ["sh", "cmd"]


def runner_available(runner: str) -> bool:
    if runner == "sh":
        return shutil.which("sh") is not None
    return sys.platform == "win32"


def make_shims(folder: Path, runner: str, **states: str) -> None:
    """Создаёт python3, python и py. Состояния: ok, broken, old; нет записи значит broken."""
    for name in ("python3", "python", "py"):
        state = states.get(name, "broken")
        if runner == "sh":
            real = REAL_PYTHON.as_posix()
            body = {
                "ok": f'exec "{real}" "$@"\n',
                "old": f'case "$1" in -c) exit 1;; esac\nexec "{real}" "$@"\n',
                "broken": "echo 'Python was not found' >&2\nexit 9\n",
            }[state]
            if name == "py" and state != "broken":
                body = 'if [ "$1" = "-3" ]; then shift; fi\n' + body
            (folder / name).write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
            (folder / name).chmod(0o755)
        else:
            real = str(REAL_PYTHON)
            ok = f'@"{real}" %*\r\n'
            body = {
                "ok": ok,
                "old": '@if "%~1"=="-c" exit /b 1\r\n' + ok,
                "broken": "@exit /b 9009\r\n",
            }[state]
            if name == "py" and state != "broken":
                body = (
                    f'@if "%~1"=="-3" ("{real}" %2 %3 %4 %5 %6 %7 %8 %9) else ("{real}" %*)\r\n'
                    if state == "ok"
                    else '@if "%~1"=="-c" exit /b 1\r\n'
                    + f'@if "%~1"=="-3" ("{real}" %2 %3 %4 %5 %6 %7 %8 %9) else ("{real}" %*)\r\n'
                )
            (folder / f"{name}.cmd").write_bytes(body.encode("utf-8"))


def launch(
    runner: str,
    script: str,
    payload: Mapping[str, object],
    project: Path,
    shims: Path,
    launcher: Path = LAUNCHER,
) -> HookResult:
    if runner == "sh":
        argv = ["sh", str(launcher), script]
    else:
        argv = ["cmd.exe", "/c", str(launcher), script]
    separator = ";" if sys.platform == "win32" else ":"
    env = {
        **os.environ,
        "PATH": f"{shims}{separator}{os.environ.get('PATH', '')}",
        "CLAUDE_PROJECT_DIR": str(project),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.run(
        argv,
        input=json.dumps({**payload, "cwd": str(project)}).encode("utf-8"),
        capture_output=True,
        env=env,
        check=False,
        timeout=120,
    )
    return HookResult(process)


@pytest.fixture(params=RUNNERS)
def runner(request: pytest.FixtureRequest) -> str:
    name: str = request.param
    if not runner_available(name):
        pytest.skip(f"{name} недоступен на этой системе")
    return name


def test_works_with_only_python3(runner: str, project: Path, tmp_path: Path) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="ok")
    result = launch(runner, "guard_destructive.py", BAD_COMMAND, project, shims)
    assert result.blocked, result.stderr
    assert "[guard_destructive]" in result.stderr


def test_skips_broken_store_stub_and_uses_python(
    runner: str, project: Path, tmp_path: Path
) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="broken", python="ok")
    result = launch(runner, "guard_destructive.py", BAD_COMMAND, project, shims)
    assert result.blocked, result.stderr


def test_falls_back_to_py_launcher(runner: str, project: Path, tmp_path: Path) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="broken", python="broken", py="ok")
    result = launch(runner, "guard_destructive.py", BAD_COMMAND, project, shims)
    assert result.blocked, result.stderr


def test_skips_python_that_is_too_old(runner: str, project: Path, tmp_path: Path) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="old", python="ok")
    assert launch(runner, "guard_destructive.py", BAD_COMMAND, project, shims).blocked


def test_hook_exit_code_and_output_pass_through(runner: str, project: Path, tmp_path: Path) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="ok")
    assert launch(runner, "guard_destructive.py", GOOD_COMMAND, project, shims).code == 0
    ask = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project / ".github" / "ci.yml")},
        "permission_mode": "default",
        "session_id": "s1",
    }
    result = launch(runner, "guard_paths.py", ask, project, shims)
    assert result.code == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_plugin_folder_with_spaces_in_path(runner: str, project: Path, tmp_path: Path) -> None:
    plugin_hooks = tmp_path / "my plugin dir" / "hooks"
    shutil.copytree(HOOKS, plugin_hooks, ignore=shutil.ignore_patterns("__pycache__"))
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner, python3="ok")
    result = launch(
        runner, "guard_destructive.py", BAD_COMMAND, project, shims, plugin_hooks / "run-hook.cmd"
    )
    assert result.blocked, result.stderr


# ---------- Python не найден вообще ----------


def test_no_python_blocks_guards_in_a_managed_project(
    runner: str, project: Path, tmp_path: Path
) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner)  # все три сломаны
    result = launch(runner, "guard_paths.py", BAD_COMMAND, project, shims)
    assert result.blocked, result.stderr
    assert NOT_FOUND[runner] in result.stderr
    assert "claude plugin disable" in result.stderr


def test_no_python_does_not_block_non_guard_hooks(
    runner: str, project: Path, tmp_path: Path
) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner)
    result = launch(runner, "audit_log.py", BAD_COMMAND, project, shims)
    assert result.code == 1  # не 2: сообщение видно, но работу не запирает
    assert NOT_FOUND[runner] in result.stderr


def test_no_python_is_silent_in_unmanaged_projects(
    runner: str, bare_project: Path, tmp_path: Path
) -> None:
    shims = tmp_path / "shims"
    shims.mkdir()
    make_shims(shims, runner)
    result = launch(runner, "guard_destructive.py", BAD_COMMAND, bare_project, shims)
    assert result.code == 0
    assert result.stderr == ""


# ---------- сам файл и конфигурация ----------


def test_launcher_is_a_polyglot_with_lf_line_endings() -> None:
    data = LAUNCHER.read_bytes()
    assert b"\r" not in data  # CR в части для sh ломает запуск
    assert data.startswith(b": << 'CMDBLOCK'\n")
    assert b"\nCMDBLOCK\n" in data


def test_whole_file_is_plain_ascii_and_batch_half_has_no_comments() -> None:
    """cmd при кодовой странице UTF-8 неверно разбирает любые не-ASCII байты в .cmd.

    Так упал CI на Windows: кириллица в комментариях ломала разбор строк batch-части.
    """
    text = LAUNCHER.read_bytes().decode("ascii")
    batch = text.split("\nCMDBLOCK\n", 1)[0]
    assert "rem " not in batch.lower()


def test_hooks_json_runs_every_script_through_the_launcher() -> None:
    hooks = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    prefix = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" '
    scripts: set[str] = set()
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"].startswith(prefix), hook["command"]
                assert "args" not in hook  # форма с оболочкой: sh сам берёт аргумент
                scripts.add(hook["command"].removeprefix(prefix))
    on_disk = {p.name for p in HOOKS.glob("*.py") if not p.name.startswith("_")}
    assert scripts == on_disk
