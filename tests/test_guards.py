"""Охранные hooks: попытка нарушения -> блок (код 2) -> понятное сообщение агенту."""

import json
from pathlib import Path

import pytest
from _common import managed_root
from conftest import CONSTITUTION, HOOKS, HookResult, bash, file_call, powershell, run_hook

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
    "git reset --hard",
    "git reset --hard HEAD~3",
    "git -C repo reset --hard origin/main",
    "git clean -f",
    "git clean -fd",
    "git clean -fdx",
    "git clean -xdf",
    "git clean -d",
    "git clean -x",
    "git clean -X",
    "git clean --force",
    "git -C repo clean -fd",
    "cd app && git clean -fdx",
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
    "git reset --soft HEAD~1",
    "git reset HEAD file.txt",
    "git clean -n",
    "git clean -nd",
    "git clean --dry-run",
    "git status",
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


# ---------- guard_paths: тесты правят только architect и tester ----------

TEST_PATHS = [
    "tests/test_x.py",
    "src/test_foo.py",
    "src/foo.test.ts",
    "web/app.spec.tsx",
    "Foo.Tests/BarTests.cs",
    "Tests/Test_Upper.py",
]
NO_TEST_ACCESS = [None, IMPLEMENTER, "parch:reviewer", "parch:janitor", "parch:explorer"]
TEST_ROLES = ["parch:architect", "parch:tester"]


@pytest.mark.parametrize("rel", TEST_PATHS)
@pytest.mark.parametrize("role", NO_TEST_ACCESS)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_only_architect_and_tester_may_edit_tests(
    rel: str, role: str | None, tool: str, project: Path
) -> None:
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, role)
    assert_blocked(result, "guard_paths", "architect и tester", rel.lower())


def test_main_session_without_role_is_told_so(project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Edit", project / "tests/test_x.py"), project)
    assert_blocked(result, "guard_paths", "основная сессия без роли")


@pytest.mark.parametrize("rel", TEST_PATHS)
@pytest.mark.parametrize("role", TEST_ROLES)
def test_architect_and_tester_may_edit_tests(rel: str, role: str, project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, role)
    assert result.code == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("role", NO_TEST_ACCESS)
@pytest.mark.parametrize(
    "command",
    [
        "echo x > tests/test_a.py",
        "rm tests/test_a.py",
        "git checkout -- tests/test_a.py",
        "cp patch.py tests/test_b.py",
        "sed -i s/a/b/ src/test_foo.py",
    ],
)
def test_shell_writes_to_tests_are_blocked_without_test_role(
    command: str, role: str | None, project: Path
) -> None:
    result = run_hook("guard_paths.py", bash(command), project, role)
    assert_blocked(result, "guard_paths", "architect и tester")


def test_architect_may_write_tests_through_shell(project: Path) -> None:
    result = run_hook("guard_paths.py", bash("echo x > tests/test_a.py"), project, TEST_ROLES[0])
    assert result.code == 0, result.stderr


# ---------- guard_paths: защищённые пути снимает только владелец ----------

GATED = [
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/hooks/guard.py",
    ".github/workflows/ci.yml",
    ".github/parch/parch_ci.py",
    "state/features.json",
    "state/baseline.json",
    "state/jscpd-baseline.json",
    "docs/CONSTITUTION.md",
    "docs/adr/0001-accepted.md",
]
EVERY_ROLE = [None, IMPLEMENTER, "parch:architect", "parch:tester", "parch:reviewer"]
ORDINARY = ["src/app.py", "docs/adr/0002-proposed.md", "docs/adr/0009-new.md", "docs/notes.md"]


def assert_asks_owner(result: HookResult, rel: str) -> None:
    assert result.code == 0, result.stderr
    answer = json.loads(result.stdout)["hookSpecificOutput"]
    assert answer["hookEventName"] == "PreToolUse"
    assert answer["permissionDecision"] == "ask"  # не allow: подтвердить может только владелец
    assert "Нужно подтверждение владельца" in answer["permissionDecisionReason"]
    assert rel in answer["permissionDecisionReason"]


@pytest.mark.parametrize("rel", GATED)
@pytest.mark.parametrize("role", EVERY_ROLE)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_protected_paths_need_owner_confirmation_for_every_role(
    rel: str, role: str | None, tool: str, project: Path
) -> None:
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, role)
    assert_asks_owner(result, rel.lower())


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "auto"])
def test_prompting_modes_show_the_owner_a_question(mode: str, project: Path) -> None:
    call = file_call("Edit", project / ".github" / "workflows" / "ci.yml")
    result = run_hook("guard_paths.py", call, project, permission_mode=mode)
    assert_asks_owner(result, ".github/workflows/ci.yml")


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk", None])
def test_without_a_way_to_ask_the_owner_the_change_is_forbidden(
    mode: str | None, project: Path
) -> None:
    call = file_call("Edit", project / ".github" / "workflows" / "ci.yml")
    result = run_hook("guard_paths.py", call, project, permission_mode=mode)
    assert_blocked(result, "guard_paths", "Спросить владельца сейчас нельзя")


def test_agent_cannot_unlock_protected_paths_by_itself(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Известные «обходы»: переменная окружения и лишние поля во входе не снимают защиту."""
    monkeypatch.setenv("PARCH_UNLOCK", "1")
    monkeypatch.setenv("PARCH_OWNER_APPROVED", "1")
    call = file_call("Edit", project / ".github" / "workflows" / "ci.yml")
    call["owner_approved"] = True
    call["tool_input"]["owner_approved"] = True
    assert_asks_owner(run_hook("guard_paths.py", call, project, IMPLEMENTER), ".github")
    blocked = run_hook("guard_paths.py", call, project, IMPLEMENTER, permission_mode="dontAsk")
    assert_blocked(blocked, "guard_paths")


@pytest.mark.parametrize(
    "command",
    [
        "echo x >> .github/workflows/ci.yml",
        "sed -i s/a/b/ .github/workflows/ci.yml",
        "tee state/features.json",
        "rm .claude/settings.json",
        "mv docs/adr/0001-accepted.md docs/adr/old.md",
    ],
)
@pytest.mark.parametrize("role", [None, IMPLEMENTER, "parch:architect"])
def test_shell_writes_to_protected_paths_need_owner_confirmation(
    command: str, role: str | None, project: Path
) -> None:
    result = run_hook("guard_paths.py", bash(command), project, role)
    assert result.code == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("rel", ORDINARY)
@pytest.mark.parametrize("role", EVERY_ROLE)
def test_ordinary_paths_are_free_for_every_role(rel: str, role: str | None, project: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, role)
    assert result.code == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests -q",
        "cat tests/test_a.py",
        "cp tests/test_a.py /tmp/copy.py",
        "ls .github",
        "python -m ruff check .",
    ],
)
def test_harmless_shell_passes_for_every_role(command: str, project: Path) -> None:
    for role in (None, IMPLEMENTER):
        assert run_hook("guard_paths.py", bash(command), project, role).code == 0


def test_powershell_writes_are_checked(project: Path) -> None:
    result = run_hook("guard_paths.py", powershell("Set-Content tests/test_a.py 'x'"), project)
    assert_blocked(result, "guard_paths", "architect и tester")
    ci = run_hook("guard_paths.py", powershell("Set-Content .github/ci.yml 'x'"), project)
    assert json.loads(ci.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


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


# ---------- guard_paths: перевод ADR в accepted делает только владелец ----------

ADR_PROPOSED = "docs/adr/0002-proposed.md"


def edit_call(path: Path, old: str, new: str) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path), "old_string": old, "new_string": new},
    }


@pytest.mark.parametrize("role", EVERY_ROLE)
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("- Статус: proposed", "- Статус: accepted"),
        ("- Статус: proposed", "- Status: Accepted (утверждено)"),
        ("proposed", "accepted"),
        ("proposed", "принят владельцем"),
    ],
)
def test_agent_cannot_flip_an_adr_to_accepted_on_its_own(
    old: str, new: str, role: str | None, project: Path
) -> None:
    result = run_hook("guard_paths.py", edit_call(project / ADR_PROPOSED, old, new), project, role)
    assert_asks_owner(result, ADR_PROPOSED)
    assert (
        "ставит только владелец"
        in json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_new_adr_cannot_be_created_already_accepted(project: Path) -> None:
    call = file_call("Write", project / "docs" / "adr" / "0009-new.md")
    call["tool_input"]["content"] = "# ADR-0009\n\n- Статус: accepted\n"
    assert_asks_owner(run_hook("guard_paths.py", call, project), "docs/adr/0009-new.md")


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/proposed/accepted/ docs/adr/0002-proposed.md",
        "echo '- Статус: accepted' >> docs/adr/0002-proposed.md",
    ],
)
def test_shell_cannot_flip_an_adr_to_accepted(command: str, project: Path) -> None:
    result = run_hook("guard_paths.py", bash(command), project)
    assert result.code == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_ordinary_adr_edits_stay_free(project: Path) -> None:
    call = edit_call(project / ADR_PROPOSED, "Контекст", "Контекст решения")
    assert run_hook("guard_paths.py", call, project).code == 0
    still_proposed = edit_call(project / ADR_PROPOSED, "- Статус: proposed", "- Статус: proposed ")
    assert run_hook("guard_paths.py", still_proposed, project).code == 0


# ---------- проект определяется по текущей папке и пути, а не только по папке сессии ----------


def test_temporary_project_is_protected_even_when_the_session_project_is_not(
    tmp_path: Path,
) -> None:
    session = tmp_path / "session"
    session.mkdir()
    other = tmp_path / "other"
    (other / "docs").mkdir(parents=True)
    (other / "docs" / "CONSTITUTION.md").write_text(CONSTITUTION, encoding="utf-8")
    note = file_call("Write", other / "notes.md")
    assert_blocked(run_hook("guard_paths.py", note, session), "guard_paths", ".md")
    blocked = run_hook("guard_packages.py", bash("pip install flask"), session, cwd=str(other))
    assert_blocked(blocked, "guard_packages", "flask")
    tests_edit = file_call("Write", other / "tests" / "test_x.py")
    assert_blocked(run_hook("guard_paths.py", tests_edit, session), "guard_paths", "architect")
    free = file_call("Write", session / "notes.md")
    assert run_hook("guard_paths.py", free, session).code == 0


def test_relative_paths_are_resolved_from_the_current_folder(project: Path) -> None:
    sub = project / "sub"
    sub.mkdir()
    result = run_hook("guard_paths.py", bash("echo x >> ../.github/ci.yml"), project, cwd=str(sub))
    assert result.code == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_docs_folder_is_not_mistaken_for_a_project_root(project: Path) -> None:
    assert managed_root(project / "docs") == project
    assert managed_root(project / "docs" / "adr") == project
    assert managed_root(project.parent) is None or managed_root(project.parent) != project / "docs"


# ---------- guard_paths: настройки проверок спрашивают владельца ----------

SETTINGS_FILES = [
    "ruff.toml",
    ".ruff.toml",
    "pyrightconfig.json",
    ".coveragerc",
    "pytest.ini",
    ".importlinter",
    "tox.ini",
    "tsconfig.json",
    "tsconfig.build.json",
    ".eslintrc.json",
    "eslint.config.js",
    "biome.json",
    ".prettierrc",
    "prettier.config.js",
    "vitest.config.ts",
    "jest.config.js",
    ".dependency-cruiser.cjs",
    "knip.json",
    ".jscpd.json",
    ".editorconfig",
    "Directory.Build.props",
    "App.ruleset",
    "x.globalconfig",
    "global.json",
    "stylecop.json",
    "PSScriptAnalyzerSettings.psd1",
    "PesterConfiguration.psd1",
    "src/web/tsconfig.json",
]
PYPROJECT = """[project]
name = "demo"
dependencies = ["requests"]

[tool.ruff]
line-length = 100

[tool.pyright]
typeCheckingMode = "strict"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.coverage.run]
branch = true

[tool.importlinter]
root_package = "demo"
"""


def write_call(path: Path, content: str) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(path), "content": content},
    }


def asks(result: HookResult) -> bool:
    return (
        result.code == 0
        and bool(result.stdout.strip())
        and json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    )


@pytest.mark.parametrize("rel", SETTINGS_FILES)
@pytest.mark.parametrize("role", EVERY_ROLE)
def test_settings_files_need_owner_confirmation_for_every_role(
    rel: str, role: str | None, project: Path
) -> None:
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, role)
    assert asks(result), result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "настройки проверок" in reason


def settings_edit(project: Path, rel: str, old: str, new: str, text: str) -> HookResult:
    (project / rel).parent.mkdir(parents=True, exist_ok=True)
    (project / rel).write_text(text, encoding="utf-8")
    return run_hook("guard_paths.py", edit_call(project / rel, old, new), project)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("line-length = 100", "line-length = 300"),
        ('typeCheckingMode = "strict"', 'typeCheckingMode = "off"'),
        ('testpaths = ["tests"]', 'testpaths = ["nothing"]'),
        ("branch = true", 'branch = false\nomit = ["src/*"]'),
        ('root_package = "demo"', 'root_package = "other"'),
    ],
)
def test_editing_a_check_section_of_pyproject_asks_the_owner(
    project: Path, old: str, new: str
) -> None:
    assert asks(settings_edit(project, "pyproject.toml", old, new, PYPROJECT))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('dependencies = ["requests"]', 'dependencies = ["requests", "rich"]'),
        ('name = "demo"', 'name = "demo2"'),
    ],
)
def test_editing_unrelated_parts_of_pyproject_is_free(project: Path, old: str, new: str) -> None:
    result = settings_edit(project, "pyproject.toml", old, new, PYPROJECT)
    assert result.code == 0 and result.stdout == "", result.stdout


def test_writing_a_whole_pyproject_compares_the_check_sections(project: Path) -> None:
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    same_settings = PYPROJECT.replace('name = "demo"', 'name = "renamed"')
    free = run_hook(
        "guard_paths.py", write_call(project / "pyproject.toml", same_settings), project
    )
    assert free.code == 0 and free.stdout == ""
    weaker = PYPROJECT.replace('typeCheckingMode = "strict"', 'typeCheckingMode = "basic"')
    assert asks(run_hook("guard_paths.py", write_call(project / "pyproject.toml", weaker), project))
    broken = "[tool.ruff\nline-length = "
    assert asks(run_hook("guard_paths.py", write_call(project / "pyproject.toml", broken), project))


def test_multiedit_of_pyproject_is_compared_too(project: Path) -> None:
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    call = {
        "hook_event_name": "PreToolUse",
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(project / "pyproject.toml"),
            "edits": [
                {"old_string": 'name = "demo"', "new_string": 'name = "x"'},
                {"old_string": "line-length = 100", "new_string": "line-length = 500"},
            ],
        },
    }
    assert asks(run_hook("guard_paths.py", call, project))


SETUP_CFG = "[metadata]\nname = a\n[tool:pytest]\naddopts = -q\n"
PACKAGE_TEST = '{"name": "a", "scripts": {"test": "vitest run"}}'
PACKAGE_START = '{"name": "a", "scripts": {"start": "node a"}}'
PACKAGE_JEST = '{"name": "a", "jest": {"bail": 1}}'
CSPROJ = "<Project><PropertyGroup><Nullable>enable</Nullable></PropertyGroup></Project>"


@pytest.mark.parametrize(
    ("rel", "text", "old", "new", "settings"),
    [
        ("setup.cfg", SETUP_CFG, "-q", "--co", True),
        ("setup.cfg", SETUP_CFG, "name = a", "name = b", False),
        ("package.json", PACKAGE_TEST, "vitest run", "echo ok", True),
        ("package.json", PACKAGE_START, "node a", "node b", False),
        ("package.json", PACKAGE_JEST, '"bail": 1', '"bail": 0', True),
        ("package.json", '{"name": "a", "version": "1"}', '"1"', '"2"', False),
        ("App.csproj", CSPROJ, "enable", "disable", True),
        ("App.csproj", CSPROJ, "</Project>", "<ItemGroup/></Project>", False),
    ],
)
def test_other_settings_containers(
    project: Path, rel: str, text: str, old: str, new: str, settings: bool
) -> None:
    result = settings_edit(project, rel, old, new, text)
    assert asks(result) is settings, (rel, result.stdout)


@pytest.mark.parametrize(
    "command",
    [
        "sed -i s/100/300/ pyproject.toml",
        "echo 'x' >> ruff.toml",
        "echo '{}' > tsconfig.json",
        "tee .coveragerc",
    ],
)
def test_shell_writes_to_settings_ask_the_owner(project: Path, command: str) -> None:
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    assert asks(run_hook("guard_paths.py", bash(command), project))


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk"])
def test_settings_edit_is_forbidden_when_the_owner_cannot_be_asked(
    project: Path, mode: str
) -> None:
    result = run_hook(
        "guard_paths.py", file_call("Write", project / "ruff.toml"), project, permission_mode=mode
    )
    assert_blocked(result, "guard_paths", "Спросить владельца сейчас нельзя")


def test_permissions_template_asks_for_every_settings_file_pattern() -> None:
    from parch_ci import all_config_patterns

    template = json.loads(
        (HOOKS.parent / "templates" / "claude" / "settings.json").read_text(encoding="utf-8")
    )
    ask = set(template["permissions"]["ask"])
    missing = [p for p in all_config_patterns() if f"Edit({p})" not in ask]
    assert not missing, missing


# ---------- guard_packages: перенаправления вывода не принимаются за имена пакетов ----------


@pytest.mark.parametrize(
    "command",
    [
        "pip install requests 2>&1",
        "pip install requests > install.log 2>&1",
        "npm install react 2>&1 | tail -3",
        "npm install --no-audit 2>/dev/null",
        "pip install requests &> out.txt",
    ],
)
def test_redirections_are_not_taken_for_package_names(command: str, project: Path) -> None:
    result = run_hook("guard_packages.py", bash(command), project)
    assert result.code == 0, result.stderr


@pytest.mark.parametrize(
    "command",
    ["pip install flask 2>&1", "npm install lodash 2>&1 | tail -3", "pip install flask > log 2>&1"],
)
def test_unlisted_packages_are_still_blocked_next_to_redirections(
    command: str, project: Path
) -> None:
    assert run_hook("guard_packages.py", bash(command), project).blocked
