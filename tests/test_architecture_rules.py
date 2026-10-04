# ruff: noqa: E501
"""F15, PR 4: архитектурные правила по уровням и «библиотека только в одном модуле».

Плохие примеры, которые проверка `architecture` обязана остановить: нижний уровень импортирует верхний; низкоуровневая
библиотека подключена в двух модулях. Пример из подсказки проверки (`IMPORTLINTER_EXAMPLE`) сам проходит на чистом коде.
"""

import subprocess
import sys
from pathlib import Path

import parch_ci
import pytest

CI = Path(__file__).resolve().parents[1] / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

RULES = """[importlinter]
root_package = shop
include_external_packages = True

[importlinter:contract:levels]
name = Уровни: ui выше services выше db
type = layers
layers =
    shop.ui
    shop.services
    shop.db

[importlinter:contract:sqlite-only-in-db]
name = Библиотека sqlite3 подключается только в shop.db
type = forbidden
source_modules = shop
forbidden_modules = sqlite3
ignore_imports = shop.db -> sqlite3
"""


def make(root: Path, services: str = "from shop import db\n", db: str = "import sqlite3\n") -> Path:
    src = root / "src" / "shop"
    src.mkdir(parents=True)
    files = {
        "__init__.py": "",
        "ui.py": "from shop import services\n",
        "services.py": services,
        "db.py": db,
        "util.py": "X = 1\n",
    }
    for name, text in files.items():
        (src / name).write_text(text, encoding="utf-8")
    (root / ".importlinter").write_text(RULES, encoding="utf-8")
    return root


def architecture(project: Path) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(CI), "architecture", "--project", str(project)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return done.returncode, done.stdout


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    return tmp_path


def test_a_clean_project_with_levels_and_a_one_module_library_passes(shop: Path) -> None:
    code, out = architecture(make(shop))
    assert code == 0, out
    assert "контрактов: 2" in out


def test_a_lower_level_importing_a_higher_one_fails(shop: Path) -> None:
    code, out = architecture(make(shop, db="import sqlite3\nfrom shop import ui\n"))
    assert code == 1
    assert "Уровни: ui выше services выше db BROKEN" in out
    assert "shop.db -> shop.ui" in out


def test_a_low_level_library_imported_in_two_modules_fails(shop: Path) -> None:
    code, out = architecture(make(shop, services="import sqlite3\nfrom shop import db\n"))
    assert code == 1
    assert "Библиотека sqlite3 подключается только в shop.db BROKEN" in out
    assert "shop.services -> sqlite3" in out


def test_a_new_module_that_imports_the_library_is_caught_without_editing_the_rule(
    shop: Path,
) -> None:
    root = make(shop)
    (root / "src" / "shop" / "extra.py").write_text("import sqlite3\n", encoding="utf-8")
    code, out = architecture(root)
    assert code == 1 and "shop.extra -> sqlite3" in out


def test_the_hint_shows_levels_and_the_one_module_library_example() -> None:
    hint = parch_ci.IMPORTLINTER_EXAMPLE
    assert "type = layers" in hint and "include_external_packages = True" in hint
    assert "Библиотека sqlite3 подключается только в" in hint
