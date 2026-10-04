# ruff: noqa: E501
"""F15, PR 2b: что считается контрактным тестом шва и какие файлы пишут за пределы проекта.

Контрактный тест называет код обеих сторон шва (запускает или читает обе) и сверяет формат (колонки, поля, код
возврата). Тест, который только называет файл или скрипт, шов не закрывает: плохой пример должен оставлять его красным.
"""

from pathlib import Path
from typing import Any

import pytest
from test_analyze_core import analyze, plan
from test_analyze_existing import call, write

mapmod: Any = __import__("analyze_map")


def rel_of(project: Path) -> list[str]:
    return sorted(p.relative_to(project).as_posix() for p in project.rglob("*.*"))


@pytest.fixture
def shared(tmp_path: Path) -> Path:
    """Общий файл: panel.py пишет `autopilot.txt`, Tool.cs читает."""
    write(
        tmp_path / "ops" / "panel.py",
        'def save() -> None:\n    open("config/autopilot.txt", "w").write("task=idle")\n',
    )
    write(tmp_path / "plugin" / "Tool.cs", 'class Tool { string P = @"config\\autopilot.txt"; }\n')
    return tmp_path


@pytest.fixture
def launch(tmp_path: Path) -> Path:
    """Запуск: launch.py запускает round.ps1."""
    write(
        tmp_path / "ops" / "launch.py",
        'import subprocess\n\n\ndef go() -> None:\n    subprocess.run(["powershell", "-File", "scripts/round.ps1"])\n',
    )
    write(tmp_path / "scripts" / "round.ps1", "param()\nWrite-Output 1\n")
    return tmp_path


def only_seam(project: Path) -> dict[str, Any]:
    seams = mapmod.seams(project, rel_of(project))
    assert len(seams) == 1
    return seams[0]


# ---------- плохие примеры: только упоминание не засчитывается ----------


def test_a_test_with_one_mention_of_the_shared_file_leaves_the_seam_red(shared: Path) -> None:
    write(
        shared / "tests" / "ConfigTests.cs", 'class ConfigTests { string F = "autopilot.txt"; }\n'
    )
    write(
        shared / "tests" / "test_one_mention.py",
        "def test_x() -> None:\n    assert 'autopilot.txt'\n",
    )
    seam = only_seam(shared)
    assert seam["red"] is True and seam["contract_tests"] == []
    assert seam["weak_tests"] == ["tests/ConfigTests.cs", "tests/test_one_mention.py"]


def test_a_test_with_one_mention_of_the_launched_script_leaves_the_seam_red(launch: Path) -> None:
    write(launch / "tests" / "test_mention.py", "def test_x() -> None:\n    assert 'round.ps1'\n")
    seam = only_seam(launch)
    assert seam["red"] is True and seam["weak_tests"] == ["tests/test_mention.py"]


def test_a_test_about_only_one_side_does_not_close_the_seam(shared: Path) -> None:
    write(
        shared / "tests" / "test_panel_only.py",
        "def test_header() -> None:\n    from ops import panel\n    header = open('config/autopilot.txt').readline()\n    assert header.startswith('task=') and panel\n",
    )
    assert only_seam(shared)["red"] is True  # про panel.py есть, про Tool.cs нет


def test_a_test_that_touches_both_sides_but_checks_no_format_does_not_close_the_seam(
    launch: Path,
) -> None:
    write(
        launch / "tests" / "test_no_check.py",
        "import subprocess\n\nfrom ops import launch\n\n\ndef test_runs() -> None:\n    launch.go()\n    subprocess.run(['powershell', '-File', 'scripts/round.ps1'])\n",
    )
    write(
        launch / "tests" / "test_no_assert.py",
        "import subprocess\n\nfrom ops import launch\n\n\ndef test_runs() -> None:\n    launch.go()\n    done = subprocess.run(['powershell', '-File', 'scripts/round.ps1'])\n    print(done.returncode)\n",
    )
    seam = only_seam(launch)
    assert seam["red"] is True and len(seam["weak_tests"]) == 2


# ---------- хороший пример: обе стороны и формат ----------


def test_a_test_that_runs_both_sides_and_checks_the_exit_code_closes_the_seam(launch: Path) -> None:
    write(
        launch / "tests" / "test_round_contract.py",
        "import subprocess\n\nfrom ops import launch\n\n\ndef test_exit_code() -> None:\n    launch.go()\n    done = subprocess.run(['powershell', '-File', 'scripts/round.ps1'])\n    assert done.returncode == 0\n",
    )
    seam = only_seam(launch)
    assert seam["red"] is False and seam["contract_tests"] == ["tests/test_round_contract.py"]


def test_a_test_that_reads_both_sides_and_checks_columns_closes_the_shared_file_seam(
    shared: Path,
) -> None:
    write(
        shared / "tests" / "test_config_contract.py",
        "from ops import panel\n\n\ndef test_columns() -> None:\n    panel.save()\n    header = open('config/autopilot.txt').readline()\n"
        "    tool = open('plugin/Tool.cs').read()\n    assert header.startswith('task=') and 'autopilot.txt' in tool\n",
    )
    seam = only_seam(shared)
    assert seam["red"] is False and seam["weak_tests"] == []


def test_names_in_comments_and_docstrings_do_not_count_as_touching_a_side(shared: Path) -> None:
    write(
        shared / "tests" / "test_comment_only.py",
        '"""Tool.cs читает autopilot.txt, panel.py его пишет."""\n\n\ndef test_columns() -> None:\n'
        "    # Tool.cs и panel.py\n    header = open('config/autopilot.txt').readline()\n    assert header.startswith('task=')\n",
    )
    seam = only_seam(shared)
    assert seam["red"] is True and seam["weak_tests"] == ["tests/test_comment_only.py"]


def test_interfaces_draft_shows_weak_tests_separately_and_keeps_the_seam_red(shared: Path) -> None:
    write(
        shared / "tests" / "test_one_mention.py",
        "def test_x() -> None:\n    assert 'autopilot.txt'\n",
    )
    text = mapmod.interfaces_markdown(mapmod.seams(shared, rel_of(shared)))
    assert (
        "**нет (красный)** (только называют шов, не засчитаны: `tests/test_one_mention.py`)" in text
    )
    assert "не засчитывается" in text and "Два теста, каждый про свою сторону" in text


# ---------- запись за пределы проекта ----------


@pytest.fixture
def deploy_project(tmp_path: Path) -> Path:
    write(
        tmp_path / "scripts" / "deploy.ps1",
        'param([string]$GameDir = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Game")\n'
        'dotnet build plugin -p:GameDir=$GameDir\n$target = Join-Path $GameDir "BepInEx\\plugins\\Mod"\n'
        "New-Item -ItemType Directory -Force $target | Out-Null\nCopy-Item plugin\\bin\\Mod.dll $target -Force\n",
    )
    write(  # управляет панелью через REST и планировщик, файлов вне проекта не пишет
        tmp_path / "scripts" / "panel-up.ps1",
        "$base = 'http://127.0.0.1:8765'\nStart-ScheduledTask -TaskName Panel\nInvoke-RestMethod -Uri ($base + '/api/launch')\n"
        "Write-Host 'schtasks /create /tn X'\n",
    )
    write(
        tmp_path / "scripts" / "local.ps1",
        "Copy-Item data\\a.txt build\\a.txt\n# Copy-Item C:\\Windows\\x D:\\y\n",
    )
    write(
        tmp_path / "ops" / "run.py",
        'import subprocess\n\n\ndef go() -> None:\n    subprocess.run(["powershell", "-File", "scripts/deploy.ps1"])\n',
    )
    write(tmp_path / "plugin" / "Mod.csproj", "<Project />\n")
    write(tmp_path / "plugin" / "Mod.cs", "class Mod { }\n")
    return tmp_path


def test_files_that_write_outside_the_project_are_found_and_the_rest_are_not(
    deploy_project: Path,
) -> None:
    found = mapmod.outside_writes(deploy_project, rel_of(deploy_project))
    assert list(found) == ["scripts/deploy.ps1"]
    assert (
        found["scripts/deploy.ps1"][0] == "scripts/deploy.ps1:3"
    )  # где путь вне проекта попал в переменную
    assert "scripts/deploy.ps1:4" in found["scripts/deploy.ps1"]  # операция записи


def test_the_map_the_seam_and_the_plan_mark_them_as_a_zone_needing_a_separate_yes(
    deploy_project: Path,
) -> None:
    facts = analyze.inventory(deploy_project, [])
    assert facts["protected_paths"] == [] and facts["protected_files_auto"] == [
        "scripts/deploy.ps1"
    ]
    seam = next(s for s in facts["seams"] if "scripts/deploy.ps1" in s["files"])
    assert seam["outside_write"] == ["scripts/deploy.ps1"]
    contract = next(r for r in facts["plan"] if r["action"] == "Контрактный тест")
    assert "шов пишет за пределы проекта (scripts/deploy.ps1)" in contract["what"]
    assert contract["risk"].startswith("неприкосновенная зона, нужно отдельное «да»")
    rename = plan.build_plan(
        [
            {
                "is_standard": False,
                "action": plan.RENAME,
                "path": "scripts/deploy.ps1",
                "standard": "scripts/install.ps1",
                "title": "t",
                "note": "n",
            }
        ],
        rel_of(deploy_project),
        ["scripts/deploy.ps1"],
    )[0]
    assert rename["needs_owner_yes"] is True and rename["risk"] == plan.ZONE_YES
    assert plan.validate([rename], ["scripts/deploy.ps1"]) == []


def test_the_drafts_list_the_files_that_write_outside_the_project(deploy_project: Path) -> None:
    call({"command": "inventory", "project_dir": str(deploy_project)})
    call({"command": "write", "project_dir": str(deploy_project)})
    folder = deploy_project / "parch-analysis"
    modules = (folder / "drafts" / "MODULES.md").read_text(encoding="utf-8")
    assert "## Файлы, которые пишут за пределы проекта" in modules
    assert (
        "| scripts/deploy.ps1 |" in modules
        and "неприкосновенная зона, нужно отдельное «да»" in modules
    )
    assert "panel-up.ps1" not in modules.split("## Файлы, которые пишут")[1]
    interfaces = (folder / "drafts" / "INTERFACES.md").read_text(encoding="utf-8")
    assert "`scripts/deploy.ps1`: неприкосновенная зона, нужно отдельное «да»" in interfaces
    plan_text = (folder / "PLAN.md").read_text(encoding="utf-8")
    assert "пишут за пределы проекта" in plan_text and "`scripts/deploy.ps1`" in plan_text
    assert (
        call({"command": "verify", "project_dir": str(deploy_project)})["ok"] is None
    )  # проект не git: проверить нельзя


def test_reading_from_outside_and_writing_inside_the_project_is_not_an_outside_write(
    tmp_path: Path,
) -> None:
    write(  # копирует журнал игры в проект: источник вне проекта, приёмник внутри
        tmp_path / "scripts" / "copy_log.ps1",
        r"$dir = Join-Path $env:USERPROFILE 'AppData\Game'"
        + "\n$log = Join-Path $dir 'Player.log'\n"
        r'$dest = Join-Path $PSScriptRoot ("data\" + $name + "_Player.log")'
        + "\nCopy-Item $log $dest -Force\n",
    )
    write(  # абсолютный путь самого проекта внутри строки: это не вне проекта
        tmp_path / "plugin" / "Dump.cs",
        'class Dump { const string OutDir = @"'
        + str(tmp_path)
        + r'\data\capture"; void Go() { Directory.CreateDirectory(OutDir); } }'
        + "\n",
    )
    write(
        tmp_path / "scripts" / "mention.ps1",
        r"$game = 'C:\Program Files\Game'"
        + "\nWrite-Host $game\n"
        + r"Set-Content data\a.txt 1"
        + "\n",
    )
    assert mapmod.outside_writes(tmp_path, rel_of(tmp_path)) == {}


def test_writing_to_the_game_folder_through_a_variable_is_found(tmp_path: Path) -> None:
    write(
        tmp_path / "ops" / "install.py",
        'import shutil\nGAME = "D:/Games/FM"\ntarget = GAME + "/plugins"\nshutil.copy("a.dll", target)\n',
    )
    write(tmp_path / "ops" / "local.py", 'import shutil\nshutil.copy("a.dll", "build/a.dll")\n')
    assert list(mapmod.outside_writes(tmp_path, rel_of(tmp_path))) == ["ops/install.py"]


def test_a_side_named_like_the_shared_file_is_not_matched_by_the_file_name_itself(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "ops" / "autopilot.py", 'open("config/autopilot.txt", "w").write("task=idle")\n'
    )
    write(
        tmp_path / "plugin" / "AutoPilot.cs",
        'class AutoPilot { string P = "config/autopilot.txt"; }\n',
    )
    write(
        tmp_path / "tests" / "test_names_only_the_file.py",
        "def test_header() -> None:\n    header = open('config/autopilot.txt').readline()\n    assert header.startswith('task=')\n",
    )
    seam = only_seam(tmp_path)
    assert (
        seam["red"] is True
    )  # слово «autopilot» в имени файла не значит, что тест трогает AutoPilot.cs и autopilot.py
