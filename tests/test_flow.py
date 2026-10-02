"""post_edit_check, stop_gate, audit_log, конфигурация hooks и правила permissions."""

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import post_edit_check
import pytest
from conftest import HOOKS, bash, file_call, run_hook

REPO = Path(__file__).resolve().parent.parent
BAD_PYTHON = "import os\n\n\ndef f(x: int) -> str:\n    return x\n"
CLEAN_PYTHON = "def f(x: int) -> str:\n    return str(x)\n"


# ---------- post_edit_check ----------


def test_post_edit_check_returns_only_error_lines(project: Path) -> None:
    target = project / "bad.py"
    target.write_text(BAD_PYTHON, encoding="utf-8")
    result = run_hook("post_edit_check.py", file_call("Write", target, "PostToolUse"), project)
    assert result.blocked, result.stderr
    assert "F401" in result.stderr  # ruff: неиспользуемый импорт
    assert (
        "not assignable to return type" in result.stderr
    )  # проверка типов: неверный тип результата
    for noise in ("Found ", "All checks passed", "0 errors", "files left unchanged"):
        assert noise not in result.stderr


def test_post_edit_check_is_quiet_on_clean_file(project: Path) -> None:
    target = project / "good.py"
    target.write_text(CLEAN_PYTHON, encoding="utf-8")
    result = run_hook("post_edit_check.py", file_call("Edit", target, "PostToolUse"), project)
    assert result.code == 0
    assert result.stderr == ""


def test_post_edit_check_reformats_file(project: Path) -> None:
    target = project / "ugly.py"
    target.write_text("x   =   1\n", encoding="utf-8")
    result = run_hook("post_edit_check.py", file_call("Write", target, "PostToolUse"), project)
    assert result.code == 0
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_post_edit_check_is_inactive_without_constitution(bare_project: Path) -> None:
    target = bare_project / "bad.py"
    target.write_text(BAD_PYTHON, encoding="utf-8")
    result = run_hook("post_edit_check.py", file_call("Write", target, "PostToolUse"), bare_project)
    assert result.code == 0


def test_run_step_filters_everything_except_error_lines(tmp_path: Path) -> None:
    script = "print('ok line'); print('a.ts:3:1: error boom'); print('Found 1 problem')"
    step = post_edit_check.Step(
        "fake",
        [sys.executable, "-c", script],
        post_edit_check.location_line,
        tmp_path,  # pyright: ignore[reportPrivateUsage]
    )
    assert post_edit_check.run_step(step) == ["a.ts:3:1: error boom"]


def test_typescript_tools_are_chosen_from_node_modules(tmp_path: Path) -> None:
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    for tool in ("prettier", "eslint", "tsc"):
        (bin_dir / tool).write_text("", encoding="utf-8")
        (bin_dir / f"{tool}.cmd").write_text("", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    file = tmp_path / "src" / "a.ts"
    file.parent.mkdir()
    file.write_text("export {}\n", encoding="utf-8")
    names = [s.name for s in post_edit_check.steps_for(file, tmp_path)]
    assert names == ["prettier", "eslint", "tsc"]


def test_csharp_and_powershell_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    def fake_which(name: str) -> str:
        return f"/fake/{name}"

    monkeypatch.setattr(post_edit_check.shutil, "which", fake_which)
    (tmp_path / "App.csproj").write_text("<Project/>", encoding="utf-8")
    cs = tmp_path / "Program.cs"
    cs.write_text("class P {}", encoding="utf-8")
    assert [s.name for s in post_edit_check.steps_for(cs, tmp_path)] == [
        "dotnet format",
        "dotnet build",
    ]
    ps = tmp_path / "run.ps1"
    ps.write_text("Write-Output 1", encoding="utf-8")
    steps = post_edit_check.steps_for(ps, tmp_path)
    assert [s.name for s in steps] == ["PSScriptAnalyzer"]
    assert steps[0].env["PARCH_FILE"] == str(ps)


# ---------- stop_gate ----------

FAILING = '- python -c "import sys; sys.exit(1)"'
FLAG_DEPENDENT = (
    "- python -c \"import pathlib, sys; print(pathlib.Path('flag').exists()); sys.exit(1)\""
)


def set_checks(project: Path, command: str) -> None:
    constitution = project / "docs" / "CONSTITUTION.md"
    constitution.write_text(
        constitution.read_text(encoding="utf-8") + f"\n## Команды проверки\n{command}\n",
        encoding="utf-8",
    )


def stop(project: Path, active: bool = False) -> Any:
    return run_hook("stop_gate.py", {"hook_event_name": "Stop"}, project, stop_hook_active=active)


def test_stop_is_blocked_while_checks_fail(project: Path) -> None:
    set_checks(project, FAILING)
    result = stop(project)
    assert result.blocked, result.stderr
    assert "Закончить нельзя" in result.stderr
    assert "sys.exit(1)" in result.stderr


def test_stop_is_allowed_when_checks_pass(project: Path) -> None:
    set_checks(project, '- python -c "print(1)"')
    assert stop(project).code == 0


def test_stop_gate_does_not_loop_on_unchanged_failure(project: Path) -> None:
    set_checks(project, FLAG_DEPENDENT)
    assert stop(project).blocked  # первая попытка: блок
    repeated = stop(project, active=True)  # те же ошибки: выпускаем, чтобы не зациклиться
    assert repeated.code == 0
    assert "зациклиться" in json.loads(repeated.stdout)["systemMessage"]
    (project / "flag").write_text("", encoding="utf-8")  # ошибки изменились: прогресс есть
    assert stop(project, active=True).blocked


def test_stop_gate_skips_clean_git_tree(project: Path) -> None:
    set_checks(project, FAILING)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], cwd=project, check=True)
    subprocess.run([*git, "add", "-A"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=project, check=True)
    assert stop(project).code == 0  # в сессии ничего не правили


def test_stop_gate_is_inactive_without_constitution(bare_project: Path) -> None:
    assert stop(bare_project).code == 0


def test_stop_gate_detects_python_project_checks(project: Path) -> None:
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_fail.py").write_text("def test_f():\n    assert False\n", "utf-8")
    result = stop(project)
    assert result.blocked
    assert "pytest" in result.stderr
    assert "assert False" in result.stderr


# ---------- audit_log ----------


def read_audit(project: Path) -> list[dict[str, Any]]:
    lines: list[str] = []
    for log in (project / ".claude" / "audit").glob("*.jsonl"):
        lines.extend(log.read_text(encoding="utf-8").splitlines())
    return [json.loads(line) for line in lines]


def test_audit_log_records_commands_and_redacts_secrets(project: Path) -> None:
    call = bash("curl -H 'Authorization: token abc123' https://x")
    assert run_hook("audit_log.py", call, project, "parch:implementer").code == 0
    records = read_audit(project)
    assert records[0]["hook"] == "audit_log"
    assert records[0]["agent"] == "parch:implementer"
    assert records[0]["decision"] == "attempt"
    assert "abc123" not in records[0]["detail"]
    assert "***" in records[0]["detail"]


def test_guard_decisions_land_in_the_audit_log(project: Path) -> None:
    assert run_hook("guard_destructive.py", bash("rm -rf build"), project).blocked
    records = [r for r in read_audit(project) if r["hook"] == "guard_destructive"]
    assert records
    assert records[0]["decision"] == "block"
    assert "rm -rf build" in records[0]["detail"]


def test_audit_log_writes_nothing_in_unmanaged_project(bare_project: Path) -> None:
    assert run_hook("audit_log.py", bash("ls"), bare_project).code == 0
    assert not (bare_project / ".claude").exists()


# ---------- hooks.json и permissions ----------


def load_hooks() -> dict[str, Any]:
    return json.loads((REPO / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8"))[
        "hooks"
    ]


def test_shell_guards_cover_both_shell_tools() -> None:
    """На Windows без Git Bash команды идут через PowerShell: matcher только Bash их бы не видел."""
    for group in load_hooks()["PreToolUse"]:
        scripts = " ".join(h["command"] for h in group["hooks"])
        if "guard_packages" in scripts or "guard_paths" in scripts:
            assert "Bash" in group["matcher"]
            assert "PowerShell" in group["matcher"]


def test_every_hook_script_has_a_test_with_a_bad_example() -> None:
    sources = " ".join(
        (REPO / "tests" / name).read_text(encoding="utf-8")
        for name in ("test_guards.py", "test_flow.py")
    )
    for script in HOOKS.glob("*.py"):
        if not script.name.startswith("_"):
            assert script.name in sources, f"нет теста для {script.name}"


def load_permissions() -> dict[str, list[str]]:
    settings = json.loads(
        (REPO / "plugin" / "templates" / "claude" / "settings.json").read_text(encoding="utf-8")
    )
    return settings["permissions"]


def carve_out_is_effective(rules: list[str]) -> bool:
    """По документации Claude Code исключение `Read(!x)` вырезает путь только из правил выше него.

    Если оно стоит раньше правила или такого правила нет, оно не вырезает ничего.
    """
    for index, rule in enumerate(rules):
        if rule.startswith("Read(!"):
            carved = rule.removeprefix("Read(!").removesuffix(")")
            earlier = [
                r for r in rules[:index] if r.startswith("Read(") and not r.startswith("Read(!")
            ]
            if not any(
                fnmatch.fnmatch(carved, r.removeprefix("Read(").removesuffix(")")) for r in earlier
            ):
                return False
    return True


def test_permissions_template_asks_owner_for_config_and_denies_the_rest() -> None:
    permissions = load_permissions()
    ask = set(permissions["ask"])
    deny = set(permissions["deny"])
    assert {
        "Edit(/.claude/settings.json)",
        "Edit(/.claude/settings.local.json)",
        "Edit(/.claude/hooks/**)",
        "Edit(/.github/**)",
        "Edit(/docs/CONSTITUTION.md)",
        "Edit(~/.claude/settings.json)",
        "Edit(~/.claude/plugins/**)",
    } <= ask
    assert {
        "Edit(/.claude/audit/**)",
        "Read(.env)",
        "Bash(rm -rf *)",
        "Bash(git push --force*)",
        "Bash(git reset --hard*)",
        "Bash(git clean -f*)",
    } <= deny
    # Правила для пути не должны быть одновременно «спросить» и «запретить»: запрет победил бы.
    assert not ask & deny
    # Путевые правила только для Edit и Read: для Write и MultiEdit Claude Code их не читает.
    assert not any(rule.startswith(("Write(", "MultiEdit(")) for rule in ask | deny)


def test_env_example_carve_out_matches_the_documented_form() -> None:
    deny = load_permissions()["deny"]
    assert "Read(.env.*)" in deny
    assert "Read(!.env.example)" in deny
    assert carve_out_is_effective(deny)


def test_carve_out_listed_before_its_rule_is_detected_as_broken() -> None:
    broken = ["Read(!.env.example)", "Read(.env.*)"]
    assert not carve_out_is_effective(broken)
    assert not carve_out_is_effective(["Read(!.env.example)"])


def test_stop_gate_finds_commands_through_path_lookup(project: Path, tmp_path: Path) -> None:
    """npm и другие `.cmd` на Windows запускаются только через поиск по PATH, а не по имени."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if os.name == "nt":
        (bindir / "mytool.cmd").write_text("@echo broken\r\n@exit /b 1\r\n", encoding="ascii")
    else:
        tool = bindir / "mytool"
        tool.write_text("#!/bin/sh\necho broken\nexit 1\n", encoding="utf-8")
        tool.chmod(0o755)
    set_checks(project, "- mytool")
    result = run_hook(
        "stop_gate.py",
        {"hook_event_name": "Stop"},
        project,
        stop_hook_active=False,
        path_prefix=bindir,
    )
    assert result.blocked, result.stderr
    assert "(код 1)" in result.stderr  # команда нашлась и сама вернула ошибку
    assert "не удалось выполнить" not in result.stderr
