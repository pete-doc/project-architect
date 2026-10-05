# ruff: noqa: E501
"""F15, хвост PR 4: правило «низкоуровневая библиотека подключается только в одном модуле» для C#, TypeScript и PowerShell.

Плохие примеры, которые проверка `libraries` обязана остановить: библиотека в двух модулях (на каждом языке), правило
с опечаткой в пути, нарушение через `require`/`using module`/`#Requires`. Подключение в разрешённом модуле, в тесте и в
комментарии не нарушение.
"""

import json
import subprocess
import sys
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rules(project: Path, *items: dict[str, object]) -> None:
    write(
        project / "state" / "architecture.json",
        json.dumps({"version": 1, "library_rules": list(items)}),
    )


def libraries(project: Path, language: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CI), "libraries", "--language", language, "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# ---------- C# ----------


def cs_project(root: Path, orders: str) -> Path:
    write(
        root / "src" / "Shop" / "Db" / "Store.cs",
        "using System.Data;\nnamespace Shop.Db;\npublic class Store { }\n",
    )
    write(root / "src" / "Shop" / "Orders" / "Service.cs", orders)
    rules(root, {"library": "System.Data", "language": "csharp", "only_in": ["src/Shop/Db"]})
    return root


def test_csharp_library_only_in_the_allowed_module_passes(tmp_path: Path) -> None:
    cs_project(tmp_path, "using System.Linq;\nnamespace Shop.Orders;\npublic class Service { }\n")
    done = libraries(tmp_path, "csharp")
    assert done.returncode == 0, done.stdout


def test_csharp_library_in_two_modules_fails(tmp_path: Path) -> None:
    cs_project(
        tmp_path, "using System.Data.Common;\nnamespace Shop.Orders;\npublic class Service { }\n"
    )
    done = libraries(tmp_path, "csharp")
    assert done.returncode == 1 and "src/Shop/Orders/Service.cs:1" in done.stdout
    assert "System.Data" in done.stdout


def test_csharp_comments_tests_and_similar_names_are_not_violations(tmp_path: Path) -> None:
    cs_project(
        tmp_path,
        "// using System.Data;\nusing System.DataVault;\nnamespace Shop.Orders;\npublic class Service { }\n",
    )
    write(tmp_path / "tests" / "Shop.Tests" / "T.cs", "using System.Data;\n")
    assert libraries(tmp_path, "csharp").returncode == 0


# ---------- TypeScript ----------


def ts_project(root: Path, util: str) -> Path:
    write(
        root / "src" / "db" / "store.ts",
        'import { readFileSync } from "node:fs";\nexport const x = readFileSync;\n',
    )
    write(root / "src" / "util" / "math.ts", util)
    rules(root, {"library": "node:fs", "language": "typescript", "only_in": ["src/db"]})
    return root


def test_typescript_library_only_in_the_allowed_module_passes(tmp_path: Path) -> None:
    ts_project(tmp_path, 'import { sum } from "../pricing";\nexport const y = sum;\n')
    assert libraries(tmp_path, "typescript").returncode == 0


def test_typescript_library_in_two_modules_fails_for_import_and_require(tmp_path: Path) -> None:
    ts_project(
        tmp_path, 'import fs from "fs";\nconst a = require("node:fs");\nexport const y = [fs, a];\n'
    )
    done = libraries(tmp_path, "typescript")
    assert done.returncode == 1
    assert "src/util/math.ts:1" in done.stdout and "src/util/math.ts:2" in done.stdout


def test_typescript_subpath_of_the_library_counts_and_tests_do_not(tmp_path: Path) -> None:
    ts_project(tmp_path, 'import { promises } from "fs/promises";\nexport const z = promises;\n')
    write(tmp_path / "src" / "util" / "math.test.ts", 'import fs from "fs";\n')
    done = libraries(tmp_path, "typescript")
    assert (
        done.returncode == 1
        and "src/util/math.ts:1" in done.stdout
        and "math.test.ts" not in done.stdout
    )


# ---------- PowerShell ----------


def ps_project(root: Path, script: str) -> Path:
    write(root / "modules" / "Db" / "Db.psm1", "Import-Module SqlServer\nfunction Get-Row { }\n")
    write(root / "scripts" / "run.ps1", script)
    rules(root, {"library": "SqlServer", "language": "powershell", "only_in": ["modules/Db"]})
    return root


def test_powershell_library_only_in_the_allowed_module_passes(tmp_path: Path) -> None:
    ps_project(tmp_path, "Import-Module Pester\n# Import-Module SqlServer\nWrite-Host 'x'\n")
    assert libraries(tmp_path, "powershell").returncode == 0


def test_powershell_library_in_two_modules_fails_for_every_way_to_load_it(tmp_path: Path) -> None:
    ps_project(
        tmp_path,
        "#Requires -Modules SqlServer\nusing module SqlServer\nImport-Module -Name 'sqlserver'\nWrite-Host 'x'\n",
    )
    done = libraries(tmp_path, "powershell")
    assert done.returncode == 1
    for line in (1, 2, 3):
        assert f"scripts/run.ps1:{line}" in done.stdout


# ---------- правила ----------


def test_a_rule_with_a_typo_in_the_path_fails(tmp_path: Path) -> None:
    cs_project(tmp_path, "namespace Shop.Orders;\npublic class Service { }\n")
    rules(tmp_path, {"library": "System.Data", "language": "csharp", "only_in": ["src/Shop/Dbb"]})
    done = libraries(tmp_path, "csharp")
    assert done.returncode == 1 and "src/Shop/Dbb" in done.stdout and "опечаткой" in done.stdout


def test_a_broken_rules_file_fails_and_a_missing_one_does_not(tmp_path: Path) -> None:
    write(tmp_path / "src" / "a.cs", "using System.Data;\n")
    assert libraries(tmp_path, "csharp").returncode == 0  # правил нет
    write(
        tmp_path / "state" / "architecture.json",
        json.dumps({"version": 1, "library_rules": [{"library": "X"}]}),
    )
    done = libraries(tmp_path, "csharp")
    assert done.returncode == 1 and "нужны library, language" in done.stdout


def test_rules_of_another_language_are_not_applied(tmp_path: Path) -> None:
    cs_project(tmp_path, "using System.Data;\nnamespace Shop.Orders;\npublic class Service { }\n")
    assert libraries(tmp_path, "typescript").returncode == 0
    assert libraries(tmp_path, "csharp").returncode == 1
