"""Проба 3 `/parch:doctor` (пакет не из списка) и определение проекта по `cd` в команде.

Случай владельца: сессия Claude Code запущена в пустой папке без CONSTITUTION.md, у команды `pip`
нет пути, поэтому hook видел только папку сессии и (правильно) не вмешивался. Теперь проба идёт
одной командой с `cd` во временный проект, и hook учитывает переход.
"""

import shutil
from pathlib import Path

import pytest
from conftest import CONSTITUTION, bash, powershell, run_hook
from test_guards import assert_blocked
from test_skills import prepare

PROBE_PACKAGE = "pip install --no-index parch-doctor-probe-package"


def managed(root: Path) -> Path:
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "CONSTITUTION.md").write_text(CONSTITUTION, encoding="utf-8")
    return root


@pytest.fixture
def session(tmp_path: Path) -> Path:
    """Папка, в которой запущена сессия: пустая, без CONSTITUTION.md."""
    folder = tmp_path / "session"
    folder.mkdir()
    return folder


def test_the_doctor_package_probe_is_blocked_when_the_session_folder_has_no_constitution(
    session: Path,
) -> None:
    result = prepare()
    project = Path(result["project"])
    try:
        probe = next(p for p in result["probes"] if p["id"] == "package")
        assert probe["command"] == f'cd "{result["project"]}" && {PROBE_PACKAGE}'
        # hook получает папку сессии как cwd: это и есть случай владельца
        blocked = run_hook("guard_packages.py", bash(probe["command"]), session, cwd=str(session))
        assert_blocked(blocked, "guard_packages", probe["expect_text"].strip("[]"))
    finally:
        shutil.rmtree(project, ignore_errors=True)


def test_the_old_probe_without_cd_shows_the_root_cause(session: Path, tmp_path: Path) -> None:
    managed(tmp_path / "project")
    done = run_hook("guard_packages.py", bash(PROBE_PACKAGE), session, cwd=str(session))
    assert done.code == 0  # папка сессии не подключена, пути в команде нет: hook не вмешивается


def test_cd_into_a_managed_project_checks_the_packages_of_that_project(
    session: Path, tmp_path: Path
) -> None:
    project = managed(tmp_path / "project")
    allowed = run_hook(
        "guard_packages.py",
        bash(f'cd "{project}" && pip install requests'),
        session,
        cwd=str(session),
    )
    assert allowed.code == 0, allowed.stderr  # пакет из списка проходит
    bad = run_hook(
        "guard_packages.py", bash(f'cd "{project}" && pip install flask'), session, cwd=str(session)
    )
    assert_blocked(bad, "guard_packages", "flask")
    relative = run_hook(
        "guard_packages.py", bash("cd project && pip install flask"), session, cwd=str(tmp_path)
    )
    assert_blocked(relative, "guard_packages", "flask")  # относительный путь считается от cwd


def test_powershell_set_location_is_understood_too(session: Path, tmp_path: Path) -> None:
    project = managed(tmp_path / "project")
    command = f'Set-Location "{project}"; pip install flask'
    done = run_hook("guard_packages.py", powershell(command), session, cwd=str(session))
    assert_blocked(done, "guard_packages", "flask")


def test_cd_to_another_folder_does_not_escape_the_protection_of_the_session_project(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")  # папка без CONSTITUTION.md
    command = f'cd "{elsewhere}" && pip install flask'
    done = run_hook("guard_packages.py", bash(command), project, cwd=str(project))
    assert_blocked(done, "guard_packages", "flask")  # проект сессии проверяется по-прежнему


def test_the_skill_tells_the_agent_to_run_the_package_probe_with_cd() -> None:
    skill = (
        Path(__file__).resolve().parent.parent / "plugin" / "skills" / "doctor" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert 'cd "<project>" && pip install --no-index parch-doctor-probe-package' in skill
    assert "CONSTITUTION.md" in skill and "одной командой с `cd`" in skill
