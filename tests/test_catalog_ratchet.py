# ruff: noqa: E501
"""F15, PR 3б: храповик каталога для существующего проекта.

Старые функции без описания допускаются (они записаны в `state/catalog-baseline.json`), число только снижается,
новая функция без описания падает. Записать новый долг может только владелец (`--accept-new`).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parents[1] / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
OLD = (
    "def legacy_one() -> int:\n    return 1\n\n\n"
    "def legacy_two() -> int:\n    return 2\n\n\n"
    'def described() -> int:\n    """Уже описана."""\n    return 3\n'
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ci(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CI), "catalog", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def debt(project: Path) -> list[str]:
    path = project / "state" / "catalog-baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))["undescribed"] if path.is_file() else []


@pytest.fixture
def old_project(tmp_path: Path) -> Path:
    write(tmp_path / "src" / "app.py", OLD)
    return tmp_path


def test_old_functions_without_description_are_allowed_once_the_owner_records_them(
    old_project: Path,
) -> None:
    assert ci(old_project).returncode == 1  # без baseline проверка строгая
    assert ci(old_project, "--update").returncode == 1  # агент сам долг не записывает
    assert debt(old_project) == []
    done = ci(old_project, "--update", "--accept-new")
    assert done.returncode == 0, done.stdout
    assert debt(old_project) == ["src/app.py:legacy_one", "src/app.py:legacy_two"]
    done = ci(old_project)
    assert done.returncode == 0 and "в долге без описания 2" in done.stdout


def test_a_new_function_without_description_fails_next_to_the_old_ones(old_project: Path) -> None:
    ci(old_project, "--update", "--accept-new")
    write(old_project / "src" / "new.py", "def brand_new() -> int:\n    return 4\n")
    done = ci(old_project)
    assert done.returncode == 1 and "src/new.py:1: brand_new: нет описания" in done.stdout
    assert "legacy_one" not in done.stdout  # старый долг не мешает
    assert ci(old_project, "--update").returncode == 1
    assert debt(old_project) == ["src/app.py:legacy_one", "src/app.py:legacy_two"]


def test_describing_an_old_function_shrinks_the_debt_and_it_cannot_come_back(
    old_project: Path,
) -> None:
    ci(old_project, "--update", "--accept-new")
    write(
        old_project / "src" / "app.py",
        OLD.replace(
            "def legacy_one() -> int:\n", 'def legacy_one() -> int:\n    """Теперь описана."""\n'
        ),
    )
    done = ci(old_project)
    assert done.returncode == 1 and "отстал от кода" in done.stdout  # каталог пополнился
    done = ci(old_project, "--update")
    assert done.returncode == 0, done.stdout
    assert debt(old_project) == ["src/app.py:legacy_two"]
    write(old_project / "src" / "app.py", OLD)  # описание убрали: снова без описания
    done = ci(old_project)
    assert done.returncode == 1 and "legacy_one" in done.stdout


def test_a_renamed_function_without_description_counts_as_new(old_project: Path) -> None:
    ci(old_project, "--update", "--accept-new")
    write(old_project / "src" / "app.py", OLD.replace("legacy_two", "legacy_2"))
    done = ci(old_project)
    assert done.returncode == 1 and "legacy_2" in done.stdout


def test_a_project_with_no_old_functions_without_description_keeps_no_baseline(
    tmp_path: Path,
) -> None:
    write(tmp_path / "src" / "ok.py", 'def fine() -> int:\n    """Описана."""\n    return 1\n')
    assert ci(tmp_path, "--update", "--accept-new").returncode == 0
    assert not (tmp_path / "state" / "catalog-baseline.json").exists()
