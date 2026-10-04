# ruff: noqa: E501
"""F15, PR 2c: красные швы делятся на две группы по риску.

(а) шов трогает игру, сейвы или `data/` (файл в неприкосновенной зоне, запись вне проекта, запись в зону, слово игры
в коде): контрактный тест отдельной строкой плана до любых изменений. (б) остальные: отдельных строк нет, условие
«сначала контрактный тест» действует, только если строка плана меняет сторону шва.
"""

from pathlib import Path
from typing import Any

import pytest
from test_analyze_core import analyze, plan
from test_analyze_existing import write

mapmod: Any = __import__("analyze_map")

PS_CALLS_PY = "python.exe tools/{name}.py\n"


def rel_of(project: Path) -> list[str]:
    return sorted(
        p.relative_to(project).as_posix() for p in project.rglob("*.*") if ".git" not in p.parts
    )


def seam_by_script(project: Path, script: str, **kwargs: Any) -> dict[str, Any]:
    found = mapmod.seams(project, rel_of(project), **kwargs)
    return next(s for s in found if f"scripts/{script}.ps1" in s["files"])


@pytest.fixture
def groups_project(tmp_path: Path) -> Path:
    for name in ("plain", "loader", "gamer", "writer", "zoned"):
        write(tmp_path / "tools" / f"{name}.py", "def run() -> None: ...\n")
    write(tmp_path / "scripts" / "plain.ps1", PS_CALLS_PY.format(name="plain"))
    write(  # читает data/, но не пишет: безопасный шов
        tmp_path / "scripts" / "loader.ps1",
        "$rows = Get-Content data\\table.csv\n" + PS_CALLS_PY.format(name="loader"),
    )
    write(  # управляет игрой
        tmp_path / "scripts" / "gamer.ps1",
        "Start-Process $steam -ArgumentList '-applaunch 1'\n" + PS_CALLS_PY.format(name="gamer"),
    )
    write(  # пишет в data/
        tmp_path / "scripts" / "writer.ps1",
        "Set-Content data\\out.txt 1\n" + PS_CALLS_PY.format(name="writer"),
    )
    write(tmp_path / "scripts" / "zoned.ps1", PS_CALLS_PY.format(name="zoned"))
    return tmp_path


def test_a_seam_that_only_reads_the_data_folder_is_group_b(groups_project: Path) -> None:
    plain = seam_by_script(groups_project, "plain", zones=["data"], markers=["applaunch"])
    loader = seam_by_script(groups_project, "loader", zones=["data"], markers=["applaunch"])
    assert plain["group"] == "б" and plain["group_reasons"] == []
    assert loader["group"] == "б"  # чтение зоны шов не делает рискованным


def test_a_seam_that_writes_the_zone_or_runs_the_game_is_group_a(groups_project: Path) -> None:
    gamer = seam_by_script(groups_project, "gamer", zones=["data"], markers=["applaunch"])
    writer = seam_by_script(groups_project, "writer", zones=["data"], markers=["applaunch"])
    assert gamer["group"] == "а" and gamer["group_reasons"] == ["scripts/gamer.ps1: «applaunch»"]
    assert writer["group"] == "а" and writer["group_reasons"] == [
        "scripts/writer.ps1: пишет в data/"
    ]


def test_a_seam_with_a_file_in_a_protected_file_zone_is_group_a(groups_project: Path) -> None:
    zoned = seam_by_script(groups_project, "zoned", zones=["scripts/zoned.ps1"])
    assert zoned["group"] == "а"
    assert zoned["group_reasons"] == ["scripts/zoned.ps1: файл в неприкосновенной зоне"]


def test_the_plan_has_contract_rows_only_for_group_a_and_keeps_group_b_as_a_condition(
    groups_project: Path,
) -> None:
    facts = analyze.inventory(
        groups_project, ["data/", "scripts/zoned.ps1"], game_markers=["applaunch"]
    )
    rows = [r for r in facts["plan"] if r["action"] == "Контрактный тест"]
    in_plan = {r["what"].split()[1] for r in rows}
    red = {s["id"]: s for s in facts["seams"] if s["red"]}
    group_a = {i for i, s in red.items() if s["group"] == "а"}
    assert group_a and group_a < set(red) and in_plan == group_a  # б среди строк нет
    zoned = next(r for r in rows if "scripts/zoned.ps1" in r["what"])
    assert zoned["risk"].startswith("неприкосновенная зона, нужно отдельное «да»")
    assert "не запускает скрипты" in zoned["verify"]
    gamer = next(r for r in rows if "scripts/gamer.ps1" in r["what"])
    assert gamer["risk"].startswith("трогает игру, сейвы или data/")


def test_a_plan_row_that_changes_a_group_b_seam_still_needs_its_contract_test_first(
    groups_project: Path,
) -> None:
    seams = mapmod.seams(groups_project, rel_of(groups_project), zones=["data"], markers=[])
    plain = next(s for s in seams if "scripts/plain.ps1" in s["files"])
    assert plain["group"] == "б" and plain["red"] is True
    eq = [
        {
            "is_standard": False,
            "action": plan.RENAME,
            "path": "tools/plain.py",
            "standard": "tools/plain_job.py",
            "title": "t",
            "note": "n",
        }
    ]
    rows = plan.build_plan(eq, rel_of(groups_project), [], seams=seams)
    rename = next(r for r in rows if r["action"] == plan.RENAME)
    assert plain["id"] in rename["seams_red"]
    assert "сначала контрактный тест шва" in rename["precondition"]
    assert plan.validate(rows, []) == []


def test_plan_markdown_shows_how_many_seams_are_in_each_group(groups_project: Path) -> None:
    facts = analyze.inventory(groups_project, ["data/"], game_markers=["applaunch"])
    red = [s for s in facts["seams"] if s["red"]]
    a = [s for s in red if s["group"] == "а"]
    b = [s for s in red if s["group"] == "б"]
    text = plan.plan_markdown(facts["plan"], facts["protected_paths"], {}, facts["seams"])
    assert f"Швы без контрактного теста: {len(red)}." in text
    assert f"**Группа (а)**, трогают игру, сейвы или `data/`: {len(a)}" in text
    assert f"**Группа (б)**, остальные: {len(b)}" in text
    assert "Группа (б): " + ", ".join(s["id"] for s in b) in text
