"""ADR-0016: новые .md под plugin/templates/ разрешены только в репозитории продукта.

Плохие примеры, которые охрана путей обязана остановить: тот же путь в обычном проекте, путь с `..`,
который ведёт наружу, похожая папка (`plugin/templates-old/`), `.md` в других местах продукта.
"""

from pathlib import Path

import pytest
from conftest import bash, file_call, run_hook


@pytest.fixture
def product(project: Path) -> Path:
    """Проект, похожий на репозиторий продукта: в корне есть плагин."""
    manifest = project / "plugin" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "parch"}\n', encoding="utf-8")
    return project


@pytest.mark.parametrize(
    "rel",
    [
        "plugin/templates/github/pull_request_template.md",
        "plugin/templates/docs/NEW.md",
        "plugin/templates/anything/deep/in/tree/readme.md",
    ],
)
def test_a_template_markdown_can_be_created_in_the_product_repo(rel: str, product: Path) -> None:
    assert run_hook("guard_paths.py", file_call("Write", product / rel), product).code == 0


@pytest.mark.parametrize(
    "rel",
    [
        "plugin/templates-old/x.md",  # похожая папка
        "plugin/hooks/notes.md",
        "plugin/notes.md",
        "templates/x.md",
        "plugin/templates/../notes.md",  # `..` выводит из plugin/templates/
        "docs/../plugin/x.md",
    ],
)
def test_other_places_in_the_product_repo_stay_blocked(rel: str, product: Path) -> None:
    result = run_hook("guard_paths.py", file_call("Write", product / rel), product)
    assert result.blocked and "[guard_paths]" in result.stderr


def test_the_same_path_stays_blocked_in_an_ordinary_project(project: Path) -> None:
    rel = "plugin/templates/github/pull_request_template.md"
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project)
    assert result.blocked and ".md" in result.stderr


def test_a_shell_write_to_a_template_markdown_follows_the_same_rule(
    product: Path, project: Path
) -> None:
    command = bash("echo hi > plugin/templates/github/x.md")
    assert run_hook("guard_paths.py", command, product).code == 0
    outside = bash("echo hi > plugin/notes.md")
    assert run_hook("guard_paths.py", outside, product).blocked
