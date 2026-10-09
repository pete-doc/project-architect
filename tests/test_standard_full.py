# ruff: noqa: E501
"""F14, PR 1: правила состава проекта в проверке `standard`: обязательные файлы, конкурирующие инструкции, `.md` вне мест.

Плохие примеры, которые проверка обязана остановить на новом проекте: нет обязательного файла, второй файл инструкций,
CLAUDE.md, не ссылающийся на AGENTS.md, `.md` вне разрешённых мест. Существующий проект сначала получает только
предупреждение; после записи долга владельцем включается храповик (старое допускается, новое падает).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

WORKFLOW = """name: ci

on:
  pull_request:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  check:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - run: echo hi
"""


CONSTITUTION = (
    "# CONSTITUTION\n\n## Целевая ОС\n\nWindows 11\n\n## Бюджеты\n\nБюджет инцидентов на блок: 2\n"
)


def write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def standard(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def managed(root: Path, existing: bool = False) -> Path:
    """Подключённый проект со всеми обязательными файлами."""
    write(root / ".github" / "parch" / "parch_ci.py", "# установленный скрипт проверок\n")
    write(root / ".github" / "workflows" / "ci.yml", WORKFLOW)
    for rel in ("docs/GOAL.md", "docs/MODULES.md", "docs/INCIDENT_TEMPLATE.md"):
        write(root / rel)
    write(root / "docs" / "CONSTITUTION.md", CONSTITUTION)
    write(root / "state" / "features.json", json.dumps({"version": 1, "features": []}))
    baseline: dict[str, object] = {"version": 1}
    if existing:
        baseline["existing_project"] = True
    write(root / "state" / "baseline.json", json.dumps(baseline))
    return root


def test_a_complete_new_project_passes(tmp_path: Path) -> None:
    done = standard(managed(tmp_path))
    assert done.returncode == 0, done.stdout


def test_a_new_project_without_a_required_file_fails_and_names_the_file(tmp_path: Path) -> None:
    root = managed(tmp_path)
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert (
        done.returncode == 1 and "docs/MODULES.md" in done.stdout and "карта модулей" in done.stdout
    )


def test_goal_and_constitution_may_live_in_the_project_root(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "GOAL.md")
    write(root / "CONSTITUTION.md", CONSTITUTION)
    (root / "docs" / "GOAL.md").unlink()
    (root / "docs" / "CONSTITUTION.md").unlink()
    assert standard(root).returncode == 0


def test_a_second_instruction_file_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / ".cursorrules")
    write(root / ".cursor" / "rules" / "style.mdc")
    done = standard(root)
    assert (
        done.returncode == 1
        and ".cursorrules" in done.stdout
        and ".cursor/rules/style.mdc" in done.stdout
    )


def test_claude_md_must_point_to_agents_md_when_both_exist(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "AGENTS.md", "# правила\n")
    write(
        root / "CLAUDE.md",
        "# свои правила Claude, расходящиеся с AGENTS\n".replace("AGENTS", "основными"),
    )
    done = standard(root)
    assert done.returncode == 1 and "CLAUDE.md не ссылается на AGENTS.md" in done.stdout
    write(root / "CLAUDE.md", "@AGENTS.md\n\n## Специфика Claude Code\n")
    assert standard(root).returncode == 0
    (root / "AGENTS.md").unlink()
    write(root / "CLAUDE.md", "# единственная инструкция\n")
    assert standard(root).returncode == 0


def test_markdown_outside_the_allowed_places_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "notes" / "plan.md")
    write(root / "src" / "NOTES.md")
    done = standard(root)
    assert done.returncode == 1 and "notes/plan.md" in done.stdout and "src/NOTES.md" in done.stdout


def test_allowed_markdown_places_do_not_fail(tmp_path: Path) -> None:
    root = managed(tmp_path)
    for rel in (
        "README.md",
        "docs/specs/F1.md",
        "state/notes.md",
        ".github/pull_request_template.md",
        ".claude/skills/a/SKILL.md",
        "plugin/agents/role.md",
        "parch-analysis/ANALYSIS_REPORT.md",
        "node_modules/pkg/README.md",
    ):
        write(root / rel)
    assert standard(root).returncode == 0


def test_a_project_without_the_installed_ci_script_is_not_subject_to_the_composition_rules(
    tmp_path: Path,
) -> None:
    write(tmp_path / ".github" / "workflows" / "ci.yml", WORKFLOW)
    write(tmp_path / "notes" / "plan.md")
    done = standard(tmp_path)
    assert done.returncode == 0 and "не применяются" in done.stdout


def test_an_existing_project_is_only_warned_until_the_owner_records_the_debt(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert (
        done.returncode == 0 and "Предупреждение" in done.stdout and "notes/plan.md" in done.stdout
    )


def test_the_owner_records_the_debt_then_new_violations_fail_and_fixed_ones_shrink_it(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    assert standard(root, "--update").returncode == 0  # без флага владельца долг не записывается
    assert not (root / "state" / "standard-baseline.json").exists()
    assert standard(root, "--update", "--accept-new").returncode == 0
    debt = json.loads((root / "state" / "standard-baseline.json").read_text(encoding="utf-8"))
    assert debt["violations"] == ["md:notes/plan.md"]
    assert standard(root).returncode == 0
    write(root / "notes" / "extra.md")
    done = standard(root)
    assert (
        done.returncode == 1
        and "notes/extra.md" in done.stdout
        and "notes/plan.md" not in done.stdout
    )
    (root / "notes" / "extra.md").unlink()
    (root / "notes" / "plan.md").unlink()
    assert standard(root, "--update").returncode == 0
    # долг погашен, но файл остался: храповик не сбрасывается, новое нарушение снова падает
    debt = json.loads((root / "state" / "standard-baseline.json").read_text(encoding="utf-8"))
    assert debt["violations"] == []
    write(root / "notes" / "again.md")
    assert standard(root).returncode == 1


def test_claude_md_that_only_mentions_agents_md_still_fails(tmp_path: Path) -> None:
    root = managed(tmp_path)
    write(root / "AGENTS.md", "# правила\n")
    write(root / "CLAUDE.md", "Не читай AGENTS.md, у меня свои правила.\n")
    done = standard(root)
    assert done.returncode == 1 and "CLAUDE.md не ссылается на AGENTS.md" in done.stdout


def test_markdown_is_not_hidden_in_nested_docs_agents_or_build_folders(tmp_path: Path) -> None:
    root = managed(tmp_path)
    hidden = (
        "src/docs/a.md",
        "src/agents/deep/b.md",
        "src/agents/x.md",
        "src/SKILL.md",
        "build/c.md",
        "env/d.md",
        "bin/e.md",
    )
    for rel in hidden:
        write(root / rel)
    done = standard(root)
    for rel in hidden:
        assert rel in done.stdout, rel


def test_a_missing_parch_standard_module_fails_loudly_instead_of_crashing(tmp_path: Path) -> None:
    import shutil

    ci = tmp_path / "ci"
    ci.mkdir()
    shutil.copy(SCRIPT, ci / "parch_ci.py")
    for name in ("parch_catalog.py", "parch_libraries.py"):
        shutil.copy(SCRIPT.parent / name, ci / name)
    root = managed(tmp_path / "proj")
    done = subprocess.run(
        [sys.executable, str(ci / "parch_ci.py"), "standard", "--project", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 1 and "parch_standard.py" in done.stdout
    assert "Traceback" not in done.stderr


def test_with_a_debt_file_a_removed_required_file_fails_even_on_an_existing_project(
    tmp_path: Path,
) -> None:
    root = managed(tmp_path, existing=True)
    write(root / "notes" / "plan.md")
    standard(root, "--update", "--accept-new")
    (root / "docs" / "MODULES.md").unlink()
    done = standard(root)
    assert done.returncode == 1 and "docs/MODULES.md" in done.stdout


def test_a_project_just_created_by_init_project_passes_the_composition_rules(
    tmp_path: Path,
) -> None:
    from test_skills import init

    init(tmp_path)
    done = standard(tmp_path)
    assert done.returncode == 0, done.stdout
    assert "не применяются" not in done.stdout  # правила действительно проверялись


def test_the_target_ci_templates_run_the_standard_check() -> None:
    for name in ("python.yml", "typescript.yml", "csharp.yml", "powershell.yml"):
        text = (REPO / "plugin" / "templates" / "ci" / "circleci" / name).read_text(
            encoding="utf-8"
        )
        assert "parch_ci.py standard" in text, name


def test_the_product_repository_itself_is_not_subject_to_the_composition_rules() -> None:
    """Продукт подключён к своим правилам: состав проверяется, найденное записано в долг.

    (Имя исторически осталось; по смыслу: правила состава к продукту применяются.)
    """
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "standard", "--project", str(REPO)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"Нарушений состава в долге: \d+", done.stdout), done.stdout
    for stale in ("не применяются", "не подключ", "не проверяются"):
        assert stale not in done.stdout, done.stdout
