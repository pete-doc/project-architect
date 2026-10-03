"""Отчёт анализа только в `parch-analysis/`: чужая папка `analysis/` остаётся нетронутой.

Случай владельца (fm26-data): в проекте уже есть папка `analysis/` с экспериментами. Прежняя
версия считала всё внутри `analysis/` разрешённым, и перезапись чужих файлов осталась бы
незамеченной.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_analyze_existing import SCRIPT, call, git, inventory, write


@pytest.fixture
def owner_repo(tmp_path: Path) -> Path:
    """Проект с чужой папкой analysis/: два файла под git и один новый, ещё не добавленный."""
    write(tmp_path / "src" / "app.py", "x = 1\n")
    write(tmp_path / "analysis" / "EXP-A01.md", "# эксперимент A01\n")
    write(tmp_path / "analysis" / "dataset.csv", "a,b\n1,2\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    write(tmp_path / "analysis" / "EXP-B02.md", "# новый эксперимент, ещё не в git\n")
    return tmp_path


def verify(project: Path) -> dict[str, Any]:
    return call({"command": "verify", "project_dir": str(project)})


def test_the_report_in_parch_analysis_passes_next_to_an_existing_analysis_folder(
    owner_repo: Path,
) -> None:
    facts = inventory(owner_repo)
    assert facts["git"]["is_repo"] is True
    write(owner_repo / "parch-analysis" / "ANALYSIS_REPORT.md", "# Отчёт\n")
    done = verify(owner_repo)
    assert done["ok"] is True and done["changed"] == []


@pytest.mark.parametrize(
    "victim", ["EXP-A01.md", "dataset.csv", "EXP-B02.md", "NEW.md"]
)  # изменён в git, изменён файл вне git (ещё не добавленный), новый файл
def test_any_write_into_the_owners_analysis_folder_is_a_violation(
    owner_repo: Path, victim: str
) -> None:
    inventory(owner_repo)
    write(owner_repo / "analysis" / victim, "перезаписано анализом\n")
    bad = verify(owner_repo)
    assert bad["ok"] is False
    assert any(f"analysis/{victim}" in line for line in bad["changed"])


def test_overwriting_an_untracked_file_is_seen_even_though_git_status_does_not_change(
    owner_repo: Path,
) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=owner_repo, capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    ).stdout  # fmt: skip
    inventory(owner_repo)
    write(owner_repo / "analysis" / "EXP-B02.md", "# другое содержимое\n")
    after = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=owner_repo, capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    ).stdout  # fmt: skip
    assert after == status  # по строкам git правка не видна: ловит только отпечаток содержимого
    assert verify(owner_repo)["ok"] is False


def test_an_unchanged_untracked_file_is_not_a_violation(owner_repo: Path) -> None:
    inventory(owner_repo)
    assert verify(owner_repo)["ok"] is True


def test_inventory_refuses_when_parch_analysis_already_exists(owner_repo: Path) -> None:
    write(owner_repo / "parch-analysis" / "OLD.md", "прежний отчёт\n")
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"command": "inventory", "project_dir": str(owner_repo)}),
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
    )  # fmt: skip
    assert done.returncode == 1 and done.stdout == ""
    assert "parch-analysis/ уже существует" in done.stderr and "спроси владельца" in done.stderr
    assert (owner_repo / "parch-analysis" / "OLD.md").read_text(
        encoding="utf-8"
    ) == "прежний отчёт\n"


def test_the_owners_analysis_notes_are_counted_as_notes_outside_docs_not_as_the_report(
    owner_repo: Path,
) -> None:
    facts = inventory(owner_repo)
    assert facts["markdown_outside_docs_total"] == 2  # EXP-A01.md и EXP-B02.md
    write(owner_repo / "parch-analysis" / "ANALYSIS_REPORT.md", "# Отчёт\n")
    assert inventory_without_report_dir(owner_repo)["markdown_outside_docs_total"] == 2


def inventory_without_report_dir(project: Path) -> dict[str, Any]:
    """Отчёт самого анализа не считается заметкой: проверяем на копии без папки отчёта."""
    report = project / "parch-analysis" / "ANALYSIS_REPORT.md"
    assert report.is_file()
    # inventory отказывается работать при существующей parch-analysis/, поэтому читаем факты
    # напрямую из модуля, а не через командную строку
    import importlib.util

    spec = importlib.util.spec_from_file_location("analyze_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result: dict[str, Any] = module.inventory(project)
    return result


def test_verify_without_inventory_is_an_error_not_a_pass(tmp_path: Path) -> None:
    write(tmp_path / "src" / "app.py", "x = 1\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"command": "verify", "project_dir": str(tmp_path)}),
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
    )  # fmt: skip
    assert done.returncode == 1 and "сначала выполни inventory" in done.stderr
