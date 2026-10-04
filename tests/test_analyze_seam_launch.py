# ruff: noqa: E501
"""F15, PR 2f: связь между языками в цепочке вызовов только настоящий запуск.

Python-файл, который называет `Helper.cs` в тексте, не запускает C#; запуском считается `subprocess`, `dotnet run`,
`Process.Start` рядом с именем файла.
"""

from pathlib import Path
from typing import Any

from test_analyze_core import analyze  # импорт подключает скрипты навыка в sys.path
from test_analyze_existing import write

SKILL_SCRIPTS = analyze.__name__
mapmod: Any = __import__("analyze_map")

RUN_PS = 'subprocess.run(["powershell", "-File", "scripts/x.ps1"])\n'


def rel_of(project: Path) -> list[str]:
    return sorted(
        p.relative_to(project).as_posix() for p in project.rglob("*.*") if ".git" not in p.parts
    )


def seam_of(project: Path) -> dict[str, Any]:
    found = mapmod.seams(project, rel_of(project), zones=[], markers=["taskkill"])
    return next(s for s in found if "ops/seam.py" in s["files"])


def project_with(tmp_path: Path, seam_py: str) -> Path:
    write(tmp_path / "scripts" / "x.ps1", "Write-Host 'x'\n")
    write(tmp_path / "plugin" / "Helper.cs", 'class Helper { void Go() { Kill("taskkill"); } }\n')
    write(tmp_path / "ops" / "seam.py", "import subprocess\n\n\ndef go() -> None:\n" + seam_py)
    return tmp_path


def test_a_python_file_that_only_names_a_csharp_file_does_not_run_it(tmp_path: Path) -> None:
    project = project_with(tmp_path, f"    # про Helper.cs\n    print('Helper.cs')\n    {RUN_PS}")
    seam = seam_of(project)
    assert seam["group"] == "б" and seam["group_reasons"] == []


def test_a_python_file_that_runs_a_csharp_file_through_dotnet_is_a_chain(tmp_path: Path) -> None:
    project = project_with(
        tmp_path, f'    subprocess.run(["dotnet", "run", "plugin/Helper.cs"])\n    {RUN_PS}'
    )
    seam = seam_of(project)
    assert seam["group"] == "а"
    assert seam["group_reasons"] == ["ops/seam.py: запускает plugin/Helper.cs («taskkill»)"]


def test_a_launch_call_far_from_the_name_is_not_a_run_of_that_file(tmp_path: Path) -> None:
    far = "    subprocess.run(['echo'])\n" + "    x = 1\n" * 5 + "    print('plugin/Helper.cs')\n"
    project = project_with(tmp_path, far + f"    {RUN_PS}")
    assert seam_of(project)["group"] == "б"
