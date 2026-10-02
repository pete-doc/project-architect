"""Охранные hooks: попытка нарушения -> блок (код 2) -> понятное сообщение агенту."""

from pathlib import Path

import pytest
from conftest import HookResult, bash, file_call, powershell, run_hook

IMPLEMENTER = "parch:implementer"


def assert_blocked(result: HookResult, hook: str, *words: str) -> None:
    assert result.blocked, f"ожидался блок, а код {result.code}: {result.stderr}"
    assert f"[{hook}]" in result.stderr
    for word in words:
        assert word in result.stderr


# ---------- guard_destructive ----------

DESTRUCTIVE = [
    "rm -rf /",
    "rm -fr build",
    "rm -r -f build",
    "rm --recursive --force build",
    "sudo rm -rf /var/lib",
    "echo hi && rm -rf build",
    "bash -c 'rm -rf build'",
    "Remove-Item C:\\x -Recurse -Force",
    "Remove-Item x -r -fo",
    "rd /s /q build",
    "git push --force",
    "git push -f origin main",
    "git push origin +main",
    "git push --force-with-lease origin main",
    "git -C repo push -fu origin main",
]
SECRETS = [
    "cat .env",
    "type .env.production",
    "cat ~/.ssh/id_rsa",
    "cp server.pem /tmp/x",
    "Get-Content .aws/credentials",
    "echo SECRET > .env",
    "printenv",
    "Get-ChildItem Env:",
]
HARMLESS = [
    "rm file.txt",
    "rm -r build",
    "rm -f note.txt",
    "git push origin main",
    "git push -u origin feature",
    "cat .env.example",
    "ls -la",
    "pytest tests -q",
    "env FOO=1 python x.py",
]


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_are_blocked(command: str, project: Path) -> None:
    result = run_hook("guard_destructive.py", bash(command), project)
    assert_blocked(result, "guard_destructive")


@pytest.mark.parametrize("command", SECRETS)
def test_secret_access_is_blocked(command: str, project: Path) -> None:
    result = run_hook("guard_destructive.py", bash(command), project)
    assert_blocked(result, "guard_destructive")


def test_destructive_message_is_actionable(project: Path) -> None:
    result = run_hook("guard_destructive.py", bash("rm -rf build"), project)
    assert_blocked(result, "guard_destructive", "нельзя отменить", "git rm")


def test_powershell_tool_is_covered(project: Path) -> None:
    result = run_hook("guard_destructive.py", powershell("Remove-Item x -Recurse -Force"), project)
    assert_blocked(result, "guard_destructive")


@pytest.mark.parametrize("command", HARMLESS)
def test_harmless_commands_pass(command: str, project: Path) -> None:
    assert run_hook("guard_destructive.py", bash(command), project).code == 0


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit"])
def test_file_tools_cannot_touch_secrets(tool: str, project: Path) -> None:
    result = run_hook("guard_destructive.py", file_call(tool, project / ".env"), project)
    assert_blocked(result, "guard_destructive", "секрет")
    assert run_hook("guard_destructive.py", file_call(tool, project / "app.py"), project).code == 0


def test_destructive_guard_works_without_project_setup(bare_project: Path) -> None:
    assert run_hook("guard_destructive.py", bash("rm -rf x"), bare_project).blocked


def test_broken_input_blocks_instead_of_passing(project: Path) -> None:
    result = run_hook("guard_destructive.py", "это не JSON", project)
    assert_blocked(result, "guard_destructive", "сломалась")


# ---------- guard_paths: роль implementer ----------

IMPLEMENTER_BLOCKED = [
    "tests/test_x.py",
    "src/test_foo.py",
    "src/foo.test.ts",
    "web/app.spec.tsx",
    "Foo.Tests/BarTests.cs",
    "Tests/Test_Upper.py",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".github/workflows/ci.yml",
    "state/features.json",
    "docs/CONSTITUTION.md",
    "docs/adr/0001-accepted.md",
]
IMPLEMENTER_ALLOWED = [
    "src/app.py",
    "docs/adr/0002-proposed.md",
    "docs/adr/0009-new.md",
    "docs/notes.md",
]


@pytest.mark.parametrize("rel", IMPLEMENTER_BLOCKED)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_implementer_cannot_edit_protected_paths(rel: str, tool: str, project: Path) -> None:
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, IMPLEMENTER)
    assert_blocked(result, "guard_paths", "implementer", rel.lower())


@pytest.mark.parametrize("rel", IMPLEMENTER_ALLOWED)
def test_implementer_can_edit_ordinary_paths(rel: str, project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, IMPLEMENTER)
    assert result.code == 0, result.stderr


@pytest.mark.parametrize("role", [None, "parch:architect", "parch:reviewer"])
def test_other_roles_may_edit_tests(role: str | None, project: Path) -> None:
    call = file_call("Write", project / "tests" / "test_x.py")
    assert run_hook("guard_paths.py", call, project, role).code == 0


IMPLEMENTER_SHELL_BLOCKED = [
    "echo x > tests/test_a.py",
    "echo x >> .github/workflows/ci.yml",
    "sed -i s/a/b/ .github/workflows/ci.yml",
    "rm tests/test_a.py",
    "git checkout -- tests/test_a.py",
    "cp patch.py tests/test_b.py",
    "tee state/features.json",
    "mv docs/adr/0001-accepted.md docs/adr/old.md",
]
IMPLEMENTER_SHELL_ALLOWED = [
    "pytest tests -q",
    "cat tests/test_a.py",
    "cp tests/test_a.py /tmp/copy.py",
    "ls .github",
    "python -m ruff check .",
]


@pytest.mark.parametrize("command", IMPLEMENTER_SHELL_BLOCKED)
def test_implementer_shell_writes_to_protected_paths_are_blocked(
    command: str, project: Path
) -> None:
    result = run_hook("guard_paths.py", bash(command), project, IMPLEMENTER)
    assert_blocked(result, "guard_paths", "защищённый путь")


@pytest.mark.parametrize("command", IMPLEMENTER_SHELL_ALLOWED)
def test_implementer_harmless_shell_passes(command: str, project: Path) -> None:
    assert run_hook("guard_paths.py", bash(command), project, IMPLEMENTER).code == 0


def test_implementer_powershell_writes_are_blocked(project: Path) -> None:
    result = run_hook(
        "guard_paths.py", powershell("Set-Content tests/test_a.py 'x'"), project, IMPLEMENTER
    )
    assert_blocked(result, "guard_paths")


# ---------- guard_paths: .md вне docs/ ----------


@pytest.mark.parametrize("rel", ["notes.md", "src/NOTES.md", "report.md", "plugin/hooks/readme.md"])
def test_new_markdown_outside_docs_is_blocked(rel: str, project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project)
    assert_blocked(result, "guard_paths", ".md", "docs/")


@pytest.mark.parametrize(
    "rel",
    [
        "docs/notes.md",
        "templates/docs/MODULES.md",
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "plugin/skills/adr/SKILL.md",
        "plugin/agents/reviewer.md",
        "analysis/ANALYSIS_REPORT.md",
        "src/code.py",
    ],
)
def test_markdown_allowed_places(rel: str, project: Path) -> None:
    assert run_hook("guard_paths.py", file_call("Write", project / rel), project).code == 0


def test_editing_existing_markdown_outside_docs_is_fine(project: Path) -> None:
    existing = project / "CHANGES.md"
    existing.write_text("# x\n", encoding="utf-8")
    assert run_hook("guard_paths.py", file_call("Edit", existing), project).code == 0


def test_shell_cannot_create_markdown_outside_docs(project: Path) -> None:
    result = run_hook("guard_paths.py", bash("echo hi > notes.md"), project)
    assert_blocked(result, "guard_paths", ".md")
    assert run_hook("guard_paths.py", bash("echo hi > docs/notes.md"), project).code == 0


def test_path_guard_is_inactive_without_constitution(bare_project: Path) -> None:
    call = file_call("Write", bare_project / "tests" / "test_x.py")
    assert run_hook("guard_paths.py", call, bare_project, IMPLEMENTER).code == 0
    assert (
        run_hook("guard_paths.py", file_call("Write", bare_project / "n.md"), bare_project).code
        == 0
    )


# ---------- guard_packages ----------

PACKAGES_BLOCKED = [
    "pip install flask",
    "pip install requests flask",
    'pip3 install "flask>=2"',
    "python -m pip install flask",
    "py -m pip install flask",
    "uv add flask",
    "uv pip install flask",
    "poetry add flask",
    "pip install git+https://github.com/x/y.git",
    "npm install lodash",
    "npm i -D @types/lodash",
    "yarn add left-pad",
    "pnpm add lodash",
    "npx some-package",
    "dotnet add package Serilog",
    "dotnet add app.csproj package Serilog",
    "nuget install Serilog",
    "Install-Module -Name PSReadLine",
    "Install-Package Foo",
    "cd app && pip install flask",
]
PACKAGES_ALLOWED = [
    "pip install requests",
    'pip install "Requests==2.31"',
    "pip install pydantic[email]>=2",
    "pip install --upgrade requests",
    "pip install -e .",
    "npm install",
    "npm i react",
    "npm i @types/node@20",
    "dotnet add package Newtonsoft.Json --version 13.0.1",
    "Install-Module Pester -Force",
    "npx --no-install eslint .",
    "pip list",
]


@pytest.mark.parametrize("command", PACKAGES_BLOCKED)
def test_unlisted_packages_are_blocked(command: str, project: Path) -> None:
    result = run_hook("guard_packages.py", bash(command), project)
    assert_blocked(result, "guard_packages", "ADR", "CONSTITUTION.md")


@pytest.mark.parametrize("command", PACKAGES_ALLOWED)
def test_listed_packages_pass(command: str, project: Path) -> None:
    result = run_hook("guard_packages.py", bash(command), project)
    assert result.code == 0, result.stderr


def test_packages_from_requirements_file_are_checked(project: Path) -> None:
    (project / "requirements.txt").write_text("requests==2.0\n# c\nflask\n", encoding="utf-8")
    result = run_hook("guard_packages.py", bash("pip install -r requirements.txt"), project)
    assert_blocked(result, "guard_packages", "flask")
    (project / "requirements.txt").write_text("requests==2.0\n", encoding="utf-8")
    assert run_hook("guard_packages.py", bash("pip install -r requirements.txt"), project).code == 0


def test_powershell_tool_install_is_checked(project: Path) -> None:
    result = run_hook("guard_packages.py", powershell("Install-Module PSReadLine"), project)
    assert_blocked(result, "guard_packages")


def test_missing_allowed_list_blocks_every_install(project: Path) -> None:
    (project / "docs" / "CONSTITUTION.md").write_text("# CONSTITUTION\n", encoding="utf-8")
    assert run_hook("guard_packages.py", bash("pip install requests"), project).blocked


def test_package_guard_is_inactive_without_constitution(bare_project: Path) -> None:
    assert run_hook("guard_packages.py", bash("pip install flask"), bare_project).code == 0
