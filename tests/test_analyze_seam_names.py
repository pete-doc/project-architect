# ruff: noqa: E501
"""F15, PR 2d: сторона шва названа в тесте только путём, импортом или вызовом, а не словом.

Дефект пилота fm26-data: слово из обычного текста теста (`run`, `harvest`, `autopilot`), равное имени файла без
расширения, засчитывало сторону шва, и три шва позеленели без настоящего теста.
"""

from pathlib import Path
from typing import Any

import pytest
from test_analyze_core import analyze  # импорт подключает скрипты навыка в sys.path
from test_analyze_existing import write

SKILL_SCRIPTS = analyze.__name__
mapmod: Any = __import__("analyze_map")

FORMAT_ASSERT = "    header = open(path).readline()\n    assert header.startswith('task=')\n"


def rel_of(project: Path) -> list[str]:
    return sorted(
        p.relative_to(project).as_posix() for p in project.rglob("*.*") if ".git" not in p.parts
    )


def only_seam(project: Path) -> dict[str, Any]:
    found = mapmod.seams(project, rel_of(project))
    assert len(found) == 1
    return found[0]


@pytest.fixture
def run_log(tmp_path: Path) -> Path:
    """C# пишет `run.log`, скрипт `scripts/run.ps1` и Python `report.py` его читают (как в fm26-data)."""
    write(tmp_path / "plugin" / "AutoPilot.cs", 'class AutoPilot { string P = "data/run.log"; }\n')
    write(tmp_path / "scripts" / "run.ps1", "Get-Content data/run.log\n")
    write(tmp_path / "scripts" / "report.py", 'LOG = "data/run.log"\n')
    return tmp_path


def test_a_test_with_only_the_word_run_does_not_close_the_run_log_seam(run_log: Path) -> None:
    write(  # слова run, autopilot, report есть, обращения к коду сторон нет
        run_log / "tests" / "test_words.py",
        "def test_run_log_header() -> None:\n    # run the autopilot and report\n    path = 'data/run.log'\n"
        + FORMAT_ASSERT,
    )
    seam = only_seam(run_log)
    assert seam["red"] is True and seam["contract_tests"] == []
    assert seam["weak_tests"] == ["tests/test_words.py"]


def test_a_test_that_builds_the_file_and_reads_only_one_side_does_not_close_the_seam(
    run_log: Path,
) -> None:
    write(  # как test_cycle_report.py: сам пишет файл и запускает читающий скрипт, писателя и вызывателя не касается
        run_log / "tests" / "test_reader_only.py",
        "import subprocess\n\n\ndef test_report() -> None:\n    open('data/run.log', 'w').write('x')\n"
        "    done = subprocess.run(['python', 'scripts/report.py'])\n    assert done.returncode == 0\n",
    )
    assert only_seam(run_log)["red"] is True


def test_a_test_with_a_path_an_import_or_a_call_for_every_side_closes_the_seam(
    run_log: Path,
) -> None:
    write(
        run_log / "tests" / "test_contract.py",
        "import subprocess\n\n\ndef test_log_format() -> None:\n"
        "    done = subprocess.run(['powershell', '-File', 'scripts\\\\run.ps1'])\n"
        "    subprocess.run(['python', 'scripts/report.py'])\n    assert done.returncode == 0\n"
        "    key = 'data/run.log'  # AutoPilot.cs пишет этот файл\n"
        "    header = open(key).readline()\n    assert header.startswith('x')\n"
        "    assert 'new AutoPilot()' in open('plugin/AutoPilot.cs').read()\n",
    )
    seam = only_seam(run_log)
    assert seam["red"] is False and seam["contract_tests"] == ["tests/test_contract.py"]


@pytest.mark.parametrize(
    ("language", "path", "text", "named"),
    [
        ("python", "ops/panel.py", "from ops import panel\n", True),
        ("python", "ops/panel.py", "import ops.panel\n", True),
        ("python", "ops/panel.py", "from ops.panel import write_config\n", True),
        ("python", "ops/panel.py", "x = 'panel'  # panel\n", False),
        ("python", "ops/panel.py", "path = 'ops/panel.py'\n", True),
        ("csharp", "plugin/Tool.cs", "var t = new Tool();\n", True),
        ("csharp", "plugin/Tool.cs", "Tool.Run();\n", True),
        ("csharp", "plugin/Tool.cs", "// the tool\nvar tool = 1;\n", False),
        ("powershell", "scripts/harvest.ps1", "run('scripts\\\\harvest.ps1')\n", True),
        ("powershell", "scripts/harvest.ps1", "kind = 'harvest'\n", False),
        ("powershell", "scripts/harvest.ps1", "harvest.ps1x\n", False),
        ("typescript", "web/panel.ts", "import { go } from './panel'\n", True),
        ("typescript", "web/panel.ts", "panel\n", False),
    ],
)
def test_what_counts_as_naming_the_code_of_a_side(
    language: str, path: str, text: str, named: bool
) -> None:
    assert mapmod.language_of(path) == language
    assert mapmod.references(text, path) is named
