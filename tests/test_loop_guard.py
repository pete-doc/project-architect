# ruff: noqa: E501
"""F13, PR 2: обнаружение петель и остановка сессии (STANDARD.md, 6.3; docs/specs/F13-incidents-stuck.md).

Петля: три одинаковые подписи ошибки подряд или файл, дважды вернувшийся к прежнему содержимому. После неё
в сессии разрешены только чтение и запись отчёта в `state/incidents/`; правки кода запрещены до конца сессии.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import HookResult, bash, file_call, run_hook
from test_guards import assert_blocked

SIG = "a1b2c3d4e5f6"


def log(
    project: Path, hook: str, decision: str, detail: str = "", session: str = "s1", ts: str = ""
) -> None:
    folder = project / ".claude" / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": ts or datetime.now(UTC).isoformat(timespec="seconds"),
        "session": session, "agent": "", "event": "PostToolUse", "tool": "Edit",
        "hook": hook, "decision": decision, "detail": detail,
    }  # fmt: skip
    with (folder / "2026-10-04.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def errors(project: Path, sig: str = SIG, session: str = "s1") -> None:
    log(project, "post_edit_check", "errors", f"3 строк с ошибками; sig={sig}", session)


def edit(project: Path, name: str, sha: str, session: str = "s1") -> None:
    log(project, "post_edit_check", "edit", f"{project / name} sha={sha}", session)


def write_src(project: Path, session: str = "s1") -> HookResult:
    payload = file_call("Write", project / "src" / "app.py")
    return run_hook("loop_guard.py", {**payload, "session_id": session}, project)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root, check=True, capture_output=True, timeout=60,
    )  # fmt: skip


# ---------- подпись ошибки ----------


def test_three_equal_signatures_in_a_row_stop_the_session(project: Path) -> None:
    for _ in range(3):
        errors(project)
    result = write_src(project)
    assert_blocked(result, "loop_guard", "Петля", "одна и та же ошибка 3 раза", "отчёт")
    assert "docs/INCIDENT_TEMPLATE.md" in result.stderr


def test_two_equal_signatures_do_not_stop_anything(project: Path) -> None:
    errors(project)
    errors(project)
    assert write_src(project).code == 0


def test_a_different_signature_or_a_clean_edit_breaks_the_series(project: Path) -> None:
    errors(project)
    errors(project, sig="ffffffffffff")
    errors(project)
    assert write_src(project).code == 0
    log(project, "post_edit_check", "ok", "файл")  # успешная правка прерывает ряд
    errors(project)
    errors(project)
    assert write_src(project).code == 0


def test_the_stop_gate_failures_count_as_signatures_too(project: Path) -> None:
    errors(project)
    errors(project)
    log(project, "stop_gate", "block", f"1 проверок не прошли; sig={SIG}")
    assert_blocked(write_src(project), "loop_guard", "Петля")


def test_the_loop_is_per_session(project: Path) -> None:
    for _ in range(3):
        errors(project, session="s1")
    assert write_src(project, session="s1").code == 2
    assert write_src(project, session="s2").code == 0


# ---------- правка туда-обратно ----------


def test_a_file_returning_to_its_old_content_twice_is_a_loop(project: Path) -> None:
    for sha in ("aaaa1111", "bbbb2222", "aaaa1111"):
        edit(project, "app.py", sha)
    assert write_src(project).code == 0  # один возврат допустим
    edit(project, "app.py", "bbbb2222")
    result = write_src(project)
    assert_blocked(result, "loop_guard", "туда-обратно", "app.py")


def test_different_files_do_not_add_up_to_a_loop(project: Path) -> None:
    for name, sha in (("a.py", "aaaa1111"), ("a.py", "bbbb2222"), ("a.py", "aaaa1111")):
        edit(project, name, sha)
    for name, sha in (("b.py", "cccc3333"), ("b.py", "dddd4444"), ("b.py", "cccc3333")):
        edit(project, name, sha)
    assert write_src(project).code == 0


# ---------- что разрешено после петли ----------


@pytest.fixture
def looped(project: Path) -> Path:
    for _ in range(3):
        errors(project)
    return project


def test_after_a_loop_only_reading_and_the_incident_report_are_allowed(looped: Path) -> None:
    assert write_src(looped).code == 2
    report = looped / "state" / "incidents" / "2026-10-04-F13-loop.md"
    assert run_hook("loop_guard.py", file_call("Write", report), looped).code == 0
    assert run_hook("loop_guard.py", file_call("Edit", report), looped).code == 0
    other = looped / "state" / "incidents" / "notes.txt"
    assert run_hook("loop_guard.py", file_call("Write", other), looped).code == 2
    for command in ("git status", "git log -S retry", "cat src/app.py", "git diff"):
        assert run_hook("loop_guard.py", bash(command), looped).code == 0, command
    for command in ("python src/app.py", "echo x > a.txt", "git commit -m x", "rm a.py", "pytest"):
        assert_blocked(run_hook("loop_guard.py", bash(command), looped), "loop_guard")


def test_the_report_does_not_lift_the_ban_on_code_edits(looped: Path) -> None:
    report = looped / "state" / "incidents" / "2026-10-04-F13-loop.md"
    report.parent.mkdir(parents=True)
    report.write_text("# отчёт\n", encoding="utf-8")
    assert write_src(looped).code == 2  # отчёт записан, код всё равно нельзя


def test_the_first_detection_is_recorded_once_as_a_flag(looped: Path) -> None:
    write_src(looped)
    write_src(looped)
    logs = sorted(
        (looped / ".claude" / "audit").glob("*.jsonl")
    )  # хук пишет в файл сегодняшней даты
    lines = [row for log_file in logs for row in log_file.read_text(encoding="utf-8").splitlines()]
    flags = [json.loads(x) for x in lines if '"decision": "loop"' in x]
    assert len(flags) == 1 and flags[0]["hook"] == "loop_guard"


def test_the_message_names_the_block_from_the_branch_prefix_or_asks_for_it(
    looped: Path,
) -> None:
    git(looped, "init", "-q", "-b", "F13-loop-guard")
    git(looped, "add", ".")
    git(looped, "commit", "-q", "-m", "init")
    assert "блок F13" in write_src(looped).stderr
    git(looped, "checkout", "-q", "-b", "fix-something")
    message = write_src(looped).stderr
    assert "блок не определён по ветке" in message and "NONE" in message  # без префикса тоже стоп


def test_an_unconnected_project_is_not_touched(bare_project: Path) -> None:
    for _ in range(3):
        errors(bare_project)
    assert write_src(bare_project).code == 0


# ---------- stop_gate и журнал post_edit_check ----------


def stop(project: Path) -> HookResult:
    return run_hook("stop_gate.py", {"hook_event_name": "Stop", "session_id": "s1"}, project)


def test_after_a_loop_the_session_may_end_only_with_a_report(looped: Path) -> None:
    write_src(looped)  # флаг петли записан
    blocked = stop(looped)
    assert blocked.blocked and "Была петля" in blocked.stderr
    report = looped / "state" / "incidents" / "2026-10-04-F13-loop.md"
    report.parent.mkdir(parents=True)
    report.write_text("# отчёт\n", encoding="utf-8")
    allowed = stop(looped)
    assert allowed.code == 0 and "новой сессией" in allowed.stdout


def test_post_edit_check_writes_the_edit_hash_and_the_error_signature(project: Path) -> None:
    bad = project / "bad.py"
    bad.write_text("import os\n\n\ndef f() -> int:\n    return 'x'\n", encoding="utf-8")
    for _ in range(3):
        result = run_hook("post_edit_check.py", file_call("Write", bad, "PostToolUse"), project)
        assert result.blocked
    lines = (project / ".claude" / "audit").glob("*.jsonl")
    records = [json.loads(x) for f in lines for x in f.read_text(encoding="utf-8").splitlines()]
    edits = [r for r in records if r["decision"] == "edit"]
    errs = [r for r in records if r["decision"] == "errors"]
    assert len(edits) == 3 and all(" sha=" in r["detail"] for r in edits)
    assert len(errs) == 3 and len({r["detail"].split("sig=")[1] for r in errs}) == 1
    # три одинаковые ошибки подряд из настоящей проверки останавливают правки кода
    assert_blocked(write_src(project), "loop_guard", "Петля")


def test_hooks_json_runs_loop_guard_and_the_shell_write_guard() -> None:
    config = (Path(__file__).resolve().parent.parent / "plugin" / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )
    assert "loop_guard.py" in config and "guard_shell_writes.py" in config
