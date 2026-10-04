# ruff: noqa: E501
"""F15, PR 2e: группа (а) считается и по цепочке вызовов, слова игры могут быть регулярными выражениями.

Шов попадает в (а), если скрипт шва запускает или подключает скрипт со словами игры, прямо или через один шаг.
Через два шага и больше цепочка не считается.
"""

from pathlib import Path
from typing import Any

from test_analyze_core import analyze  # импорт подключает скрипты навыка в sys.path
from test_analyze_existing import write

SKILL_SCRIPTS = analyze.__name__
mapmod: Any = __import__("analyze_map")

CALL_PY = (
    "$pad = 1\n" * 5 + "python.exe tools/job.py\n"
)  # отступ: вызов помощника не в окне строки запуска


def rel_of(project: Path) -> list[str]:
    return sorted(
        p.relative_to(project).as_posix() for p in project.rglob("*.*") if ".git" not in p.parts
    )


def seam_group(project: Path, markers: list[str]) -> dict[str, Any]:
    found = mapmod.seams(project, rel_of(project), zones=[], markers=markers)
    return next(s for s in found if "scripts/seam.ps1" in s["files"])


def project_with(tmp_path: Path, seam_text: str, **others: str) -> Path:
    write(tmp_path / "tools" / "job.py", "def job() -> None: ...\n")
    write(tmp_path / "scripts" / "seam.ps1", seam_text + CALL_PY)
    for name, text in others.items():
        write(tmp_path / "scripts" / f"{name}.ps1", text)
    return tmp_path


def test_a_seam_whose_script_runs_a_script_with_game_words_is_group_a(tmp_path: Path) -> None:
    project = project_with(tmp_path, "& .\\scripts\\helper.ps1\n", helper="taskkill /im fm.exe\n")
    seam = seam_group(project, ["taskkill"])
    assert seam["group"] == "а"
    assert seam["group_reasons"] == ["scripts/seam.ps1: запускает scripts/helper.ps1 («taskkill»)"]


def test_the_chain_counts_through_one_more_step(tmp_path: Path) -> None:
    project = project_with(
        tmp_path,
        "& .\\scripts\\first.ps1\n",
        first="& .\\scripts\\second.ps1\n",
        second="taskkill /im fm.exe\n",
    )
    seam = seam_group(project, ["taskkill"])
    assert seam["group"] == "а"
    assert seam["group_reasons"] == [
        "scripts/seam.ps1: через scripts/first.ps1 запускает scripts/second.ps1 («taskkill»)"
    ]


def test_the_chain_does_not_count_through_two_more_steps(tmp_path: Path) -> None:
    project = project_with(
        tmp_path,
        "& .\\scripts\\first.ps1\n",
        first="& .\\scripts\\second.ps1\n",
        second="& .\\scripts\\third.ps1\n",
        third="taskkill /im fm.exe\n",
    )
    seam = seam_group(project, ["taskkill"])
    assert seam["group"] == "б" and seam["group_reasons"] == []


def test_a_script_that_only_names_another_in_a_comment_is_not_a_chain(tmp_path: Path) -> None:
    project = project_with(tmp_path, "# see helper.ps1\n", helper="taskkill /im fm.exe\n")
    assert seam_group(project, ["taskkill"])["group"] == "б"


def test_a_word_of_another_language_class_is_not_a_call_between_languages(tmp_path: Path) -> None:
    write(tmp_path / "plugin" / "Helper.cs", 'class Helper { void Go() { Kill("taskkill"); } }\n')
    project = project_with(tmp_path, "# Helper( ) helper.\n$x = 'Helper(' \n")
    assert seam_group(project, ["taskkill"])["group"] == "б"


def test_a_regex_game_word_stops_a_process_only_together_with_fm(tmp_path: Path) -> None:
    stop_fm = r"re:Stop-Process[^\n]*\bfm\b|\bfm\b[^\n]*Stop-Process"
    killer = project_with(tmp_path / "a", "Stop-Process -Name fm -Force\n")
    quiet = project_with(tmp_path / "b", "Stop-Process -Name python -Force\n")
    assert seam_group(killer, [stop_fm])["group"] == "а"
    assert seam_group(quiet, [stop_fm])["group"] == "б"
