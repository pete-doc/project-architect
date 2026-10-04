# ruff: noqa: E501
"""F15, PR 2: карта модулей и швов между языками.

Тестовый проект с намеренными швами: Python запускает PowerShell, PowerShell запускает Python, C# и Python делят
файл `config/autopilot.txt`. Шов без контрактного теста красный сигнал; изменение шва идёт после контрактного теста.
"""

from pathlib import Path
from typing import Any

import pytest
from test_analyze_core import analyze, plan, report
from test_analyze_existing import call, git, write

mapmod: Any = __import__("analyze_map")


@pytest.fixture
def seam_project(tmp_path: Path) -> Path:
    write(
        tmp_path / "ops" / "launch.py",
        'import subprocess\n\n\ndef go() -> None:\n    subprocess.run(["powershell", "-File", "scripts/round.ps1"])\n',
    )
    write(tmp_path / "scripts" / "round.ps1", "param()\npython.exe tools/report.py\n")
    write(tmp_path / "tools" / "report.py", "def report() -> None: ...\n")
    write(
        tmp_path / "ops" / "panel.py",
        'def write_config() -> None:\n    open("config/autopilot.txt", "w").write("task=idle")\n',
    )
    write(
        tmp_path / "plugin" / "Tool.cs",
        'class Tool { string P = @"config\\autopilot.txt"; }\n',
    )
    write(
        tmp_path / "plugin" / "Tool.csproj",
        "<Project><Description>Плагин игры</Description></Project>\n",
    )
    write(  # настоящий контрактный тест: запускает обе стороны (launch.py и round.ps1) и сверяет код возврата
        tmp_path / "tests" / "test_launch.py",
        "import subprocess\n\nfrom ops import launch\n\n\ndef test_round_exit_code() -> None:\n    launch.go()\n"
        '    done = subprocess.run(["powershell", "-File", "scripts/round.ps1"], capture_output=True)\n'
        "    assert done.returncode == 0\n",
    )
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "c0")
    return tmp_path


def rel_of(project: Path) -> list[str]:
    return sorted(
        p.relative_to(project).as_posix() for p in project.rglob("*.*") if ".git" not in p.parts
    )


def found(project: Path) -> dict[str, dict[str, Any]]:
    result = mapmod.seams(project, rel_of(project))
    return {f"{s['kind']}|{','.join(s['keys'])}|{s['from']}>{s['to']}": s for s in result}


# ---------- швы ----------


def test_seams_are_found_between_languages_and_red_without_a_contract_test(
    seam_project: Path,
) -> None:
    seams = found(seam_project)
    launch = seams["запуск|round.ps1|python>powershell"]
    assert launch["files"][0] == "ops/launch.py" and "scripts/round.ps1" in launch["files"]
    assert launch["contract_tests"] == ["tests/test_launch.py"] and launch["red"] is False
    back = seams["запуск|report.py|powershell>python"]
    assert back["red"] is True and back["contract_tests"] == []
    shared = seams["общий файл|autopilot.txt|csharp>python"]
    assert shared["red"] is True and sorted(shared["files"]) == ["ops/panel.py", "plugin/Tool.cs"]
    assert [s["red"] for s in mapmod.seams(seam_project, rel_of(seam_project))] == [
        True,
        True,
        False,
    ]  # красные первыми


def test_a_test_that_names_the_shared_file_turns_the_seam_green(seam_project: Path) -> None:
    # PR 2b: засчитывается только тест, который называет обе стороны шва и сверяет формат; одного имени файла мало
    write(
        seam_project / "tests" / "test_config_contract.py",
        "from ops import panel\n\n\n"
        "def test_header_matches() -> None:\n    panel.save()\n    header = open('config/autopilot.txt').readline()\n"
        "    assert header.startswith('task=') and 'autopilot.txt' in open('plugin/Tool.cs').read()\n",
    )
    shared = found(seam_project)["общий файл|autopilot.txt|csharp>python"]
    assert shared["red"] is False and shared["contract_tests"] == ["tests/test_config_contract.py"]


def test_things_that_are_not_seams_are_not_reported(tmp_path: Path) -> None:
    write(
        tmp_path / "a.py",
        'import subprocess\nsubprocess.run(["python", "b.py"])\nsubprocess.run(["taskkill", "/IM", "fm.exe"])\n',
    )  # тот же язык и чужая программа
    write(tmp_path / "b.py", "x = 1\n")
    write(
        tmp_path / "c.py",
        'import subprocess\n# subprocess.run(["powershell", "x.ps1"])\nread("package.json")\n',
    )  # комментарий и общеизвестный файл
    write(tmp_path / "d.cs", 'class D { string P = "package.json"; }\n')
    write(tmp_path / "Gen" / "X.g.cs", 'class X { string P = "shared.csv"; }\n')
    write(tmp_path / "e.py", 'open("shared.csv")\n')
    assert mapmod.seams(tmp_path, rel_of(tmp_path)) == []


def test_a_project_without_seams_says_so_in_the_card(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x = 1\n")
    rows = analyze.inventory(tmp_path, [])["health_card"]
    assert {r["value"] for r in rows if r["signal"] == "Межъязыковые швы без тестов"} == {
        "швов между языками не найдено"
    }


# ---------- карточка, риски ----------


def test_red_seams_are_a_red_signal_per_language_and_a_risk(seam_project: Path) -> None:
    facts = analyze.inventory(seam_project, [])
    values = {(r["language"], r["signal"]): r["value"] for r in facts["health_card"]}
    assert (
        values[("python", "Межъязыковые швы без тестов")]
        == "КРАСНЫЙ СИГНАЛ: швов с участием языка: 3, без контрактного теста: 2"
    )
    assert (
        values[("csharp", "Межъязыковые швы без тестов")]
        == "КРАСНЫЙ СИГНАЛ: швов с участием языка: 1, без контрактного теста: 1"
    )
    assert "Швы между языками без контрактного теста: 2" in [r["title"] for r in facts["risks"]]
    green = report.health_card(
        {"python": 1},
        {},
        [],
        {"total": 0, "shown": []},
        [],
        0,
        [{"from": "python", "to": "powershell", "red": False}],
    )
    assert green[-2]["value"] == "швов с участием языка: 1, без контрактного теста: 0"


# ---------- план: контрактный тест перед изменением шва ----------


def test_the_plan_starts_with_a_contract_test_row_for_every_red_seam(seam_project: Path) -> None:
    zones = [
        "scripts/round.ps1",
        "ops/panel.py",
    ]  # оба шва трогают зону: группа (а), строка у каждого
    rows = analyze.inventory(seam_project, zones)["plan"]
    contract = [r for r in rows if r["action"] == "Контрактный тест"]
    assert len(contract) == 2 and rows[:2] == contract
    assert all(
        r["needs_owner_yes"] is False and "падает при изменении шва" in r["verify"]
        for r in contract
    )


def test_changing_a_seam_file_needs_its_contract_test_first(seam_project: Path) -> None:
    seams = mapmod.seams(seam_project, rel_of(seam_project))
    eq = [
        {
            "is_standard": False,
            "action": plan.RENAME,
            "path": "ops/panel.py",
            "standard": "ops/config_writer.py",
            "title": "Запись конфига",
            "note": "имя по стандарту",
        }
    ]
    rows = plan.build_plan(eq, rel_of(seam_project), [], seams=seams)
    rename = next(r for r in rows if r["action"] == plan.RENAME)
    assert (
        rename["seams_red"]
        and "сначала контрактный тест шва" in rename["precondition"]
        and rename["needs_owner_yes"] is True
    )
    assert plan.validate(rows, []) == []


def test_a_row_that_changes_a_red_seam_without_the_test_is_rejected(seam_project: Path) -> None:
    seams = mapmod.seams(seam_project, rel_of(seam_project))
    eq = [
        {
            "is_standard": False,
            "action": plan.RENAME,
            "path": "ops/panel.py",
            "standard": "ops/x.py",
            "title": "t",
            "note": "n",
        }
    ]
    rows = plan.build_plan(eq, rel_of(seam_project), [], seams=seams)
    next(r for r in rows if r["action"] == plan.RENAME)["precondition"] = ""
    problems = plan.validate(rows, [])
    assert any("изменение шва без контрактного теста" in p for p in problems)


# ---------- модули ----------


def test_modules_are_grouped_by_project_folder_with_purpose_status_and_tests(
    seam_project: Path,
) -> None:
    write(seam_project / "archive" / "old.py", "x = 1\n")
    write(seam_project / "data" / "tool.py", "x = 1\n")
    write(seam_project / "ops" / "README.md", "# Операции\n\nЗапуск и конфиг.\n")
    rows = {r["path"]: r for r in mapmod.modules(seam_project, rel_of(seam_project), ["data"])}
    assert (
        rows["plugin"]["languages"] == {"csharp": 1} and rows["plugin"]["purpose"] == "Плагин игры"
    )
    assert rows["ops"]["purpose"] == "Операции" and rows["ops"]["languages"] == {"python": 2}
    assert rows["tests"]["test_files"] == 1
    assert (
        rows["archive"]["status"] == "архив" and rows["data"]["status"] == "неприкосновенная зона"
    )
    assert (
        rows["tools"]["purpose"] == "описания нет: вопрос владельцу"
        and rows["tools"]["status"] == "рабочий"
    )


# ---------- черновики и границы ----------


def test_write_creates_modules_and_interfaces_drafts_only_in_the_report_folder(
    seam_project: Path,
) -> None:
    call({"command": "inventory", "project_dir": str(seam_project)})
    out = call({"command": "write", "project_dir": str(seam_project)})
    assert {"parch-analysis/drafts/MODULES.md", "parch-analysis/drafts/INTERFACES.md"} <= set(
        out["written"]
    )
    assert out["red_seams"] == 2
    interfaces = (seam_project / "parch-analysis" / "drafts" / "INTERFACES.md").read_text(
        encoding="utf-8"
    )
    assert (
        "черновик, ждёт утверждения владельца" in interfaces
        and "Швов: 3, без контрактного теста: 2" in interfaces
    )
    assert interfaces.count("**нет (красный)**") == 2 and "`tests/test_launch.py`" in interfaces
    modules = (seam_project / "parch-analysis" / "drafts" / "MODULES.md").read_text(
        encoding="utf-8"
    )
    assert "| plugin |" in modules and "Плагин игры" in modules
    assert call({"command": "verify", "project_dir": str(seam_project)})["ok"] is True


def test_the_word_node_in_ordinary_code_is_not_a_launch_of_node(tmp_path: Path) -> None:
    write(
        tmp_path / "ui.ps1",
        "$tree = Get-Node\nStart-Process -FilePath probe.exe -ArgumentList $node\n",
    )
    write(tmp_path / "run.ps1", "node tools/build.js --prod\n")
    seams = {s["files"][0]: s for s in mapmod.seams(tmp_path, ["ui.ps1", "run.ps1"])}
    assert "ui.ps1" not in seams and seams["run.ps1"]["to"] == "typescript"
