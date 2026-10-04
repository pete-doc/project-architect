# ruff: noqa: E501
"""Запись файлов проекта через командную строку запрещена, только Write и Edit (docs/LESSONS.md, автоматика).

Разрешены отчёты тестов, покрытие и временные файлы вне проекта.
"""

import tempfile
from pathlib import Path

import pytest
from conftest import bash, powershell, run_hook
from test_guards import assert_blocked

HEREDOC_WRITE = (
    "python - <<'EOF'\nfrom pathlib import Path\nPath('a.py').write_text('x = 1\\n')\nEOF"
)
CAT_HEREDOC = "cat > a.py <<'EOF'\nprint(1)\nEOF"

BLOCKED = [
    "echo x > notes.txt",
    "echo x >> .gitignore",
    "printf x 1> out.txt",
    CAT_HEREDOC,
    HEREDOC_WRITE,
    "python -c \"open('a.txt','w').write('x')\"",
    "tee out.txt",
    "sed -i s/a/b/ f.py",
    "cd src && echo x > a.py",
    "TEMP=. cat > $TEMP/x",  # подмена временной папки самой командой
]
ALLOWED = [
    "pytest -m '' --junitxml=test-report.xml",
    "echo x > test-report.xml",
    "echo x > coverage.xml",
    "python -m coverage xml -o coverage.xml",
    "echo x > .coverage",
    "echo x > $TEMP/note.txt",
    "echo x > /tmp/a.txt",
    "python script.py > /dev/null 2>&1",
    "make 2>&1",
    "echo hi",
    "git status",
    "python - <<'EOF'\nimport json\nprint(json.load(open('f.json')))\nEOF",
    "python - <<'EOF'\nopen('test-report.xml', 'w').write('x')\nEOF",
    "cat > $TEMP/pr.md <<'EOF'\nтекст\nEOF",
    'git commit -m "Добавить write_text( в описание"',
]


@pytest.mark.parametrize("command", BLOCKED)
def test_writing_a_project_file_from_the_command_line_is_blocked(
    command: str, project: Path
) -> None:
    result = run_hook("guard_shell_writes.py", bash(command), project)
    assert_blocked(result, "guard_shell_writes", "Write или Edit")


@pytest.mark.parametrize("command", ALLOWED)
def test_reports_coverage_temp_files_and_reading_pass(command: str, project: Path) -> None:
    result = run_hook("guard_shell_writes.py", bash(command), project)
    assert result.code == 0, result.stderr


def test_a_literal_temp_path_outside_the_project_passes_but_inside_it_does_not(
    project: Path,
) -> None:
    outside = Path(tempfile.gettempdir()) / "parch-note-outside.txt"
    assert run_hook("guard_shell_writes.py", bash(f'echo x > "{outside}"'), project).code == 0
    inside = project / "docs" / "note.txt"
    assert_blocked(
        run_hook("guard_shell_writes.py", bash(f'echo x > "{inside}"'), project),
        "guard_shell_writes",
    )


@pytest.mark.parametrize(
    "command", ["Set-Content f.txt hi", "Add-Content f.txt hi", "'x' | Out-File f.txt"]
)
def test_powershell_writers_are_blocked_too(command: str, project: Path) -> None:
    result = run_hook("guard_shell_writes.py", powershell(command), project)
    assert_blocked(result, "guard_shell_writes", "Write или Edit")


def test_the_guard_is_silent_in_a_project_without_the_plugin(bare_project: Path) -> None:
    assert run_hook("guard_shell_writes.py", bash("echo x > notes.txt"), bare_project).code == 0


def test_the_product_repository_itself_is_covered() -> None:
    repo = Path(__file__).resolve().parent.parent
    result = run_hook("guard_shell_writes.py", bash("echo x > notes.txt"), repo)
    assert_blocked(result, "guard_shell_writes")


def test_lessons_move_the_heredoc_error_to_closed_and_agents_md_states_the_rule() -> None:
    repo = Path(__file__).resolve().parent.parent
    lessons = (repo / "docs" / "LESSONS.md").read_text(encoding="utf-8")
    open_part, closed = lessons.split("## Закрыто автоматикой", 1)
    assert "heredoc" not in open_part.split("## Журнал процессных ошибок ИИ", 1)[1]
    assert "guard_shell_writes.py" in closed and "tests/test_shell_writes.py" in closed
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "только инструментами Write и Edit" in agents and "guard_shell_writes" in agents
