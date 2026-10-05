# ruff: noqa: E501
"""F18, PR 1: связь «модуль → блок → цель» (колонка «Блок» в docs/MODULES.md).

Плохие примеры, которые проверка `modules` обязана остановить: модуль без блока на новом проекте, блок, которого нет в
`features.json`; старый долг допускается под храповиком, новый модуль без блока нет; `keep-until` освобождён.
"""

import json
import subprocess
import sys
from pathlib import Path

import parch_status
import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

HEADER = "| Модуль | Путь | Назначение | Язык | Статус | Блок |\n|---|---|---|---|---|---|\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def modules(project: Path, *rows: str) -> None:
    write(
        project / "docs" / "MODULES.md", "# MODULES\n\n" + HEADER + "".join(r + "\n" for r in rows)
    )


def package(project: Path, name: str) -> None:
    write(project / "src" / name / "__init__.py", '"""Пакет."""\n')


def features(project: Path, *ids: str) -> None:
    items = [{"id": i, "title": i, "goal": ["G1"], "status": "in_progress"} for i in ids]
    write(project / "state" / "features.json", json.dumps({"version": 1, "features": items}))


def ci(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CI), "modules", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def debt(project: Path) -> list[str]:
    path = project / "state" / "modules-baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))["without_block"] if path.is_file() else []


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    package(tmp_path, "shop")
    package(tmp_path, "billing")
    features(tmp_path, "F1", "F2")
    return tmp_path


def test_the_template_has_the_block_column() -> None:
    text = (ROOT / "plugin" / "templates" / "docs" / "MODULES.md").read_text(encoding="utf-8")
    assert "| Модуль | Путь | Назначение (одной фразой) | Язык | Статус | Блок |" in text


def test_modules_with_existing_blocks_pass(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | F1, F2 |",
    )
    done = ci(shop)
    assert done.returncode == 0, done.stdout


def test_a_module_without_a_block_fails_on_a_new_project(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | ? |",
    )
    done = ci(shop)
    assert done.returncode == 1 and "src/billing" in done.stdout and "нет блока" in done.stdout


def test_a_block_that_does_not_exist_fails(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | F99 |",
    )
    done = ci(shop)
    assert done.returncode == 1 and "src/billing: блок F99" in done.stdout


def test_keep_until_frees_a_module_from_having_a_block(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | keep-until:2027-01-01 | |",
    )
    assert ci(shop).returncode == 0


def test_a_project_without_the_block_column_is_only_warned(tmp_path: Path) -> None:
    package(tmp_path, "shop")
    write(
        tmp_path / "docs" / "MODULES.md",
        "| Модуль | Путь | Назначение | Язык | Статус |\n|---|---|---|---|---|\n| shop | src/shop | Магазин | Python | active |\n",
    )
    done = ci(tmp_path)
    assert done.returncode == 0 and "нет колонки «Блок»" in done.stdout


def test_old_modules_without_a_block_sit_under_the_ratchet(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | |",
    )
    assert ci(shop).returncode == 1
    assert ci(shop, "--update").returncode == 1  # агент сам долг не записывает
    assert debt(shop) == []
    assert ci(shop, "--update", "--accept-new").returncode == 0
    assert debt(shop) == ["src/billing"]
    assert ci(shop).returncode == 0
    package(shop, "extra")
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | |",
        "| extra | src/extra | Новое | Python | active | |",
    )
    done = ci(shop)
    assert done.returncode == 1 and "src/extra" in done.stdout and "src/billing" not in done.stdout


def test_giving_an_old_module_a_block_shrinks_the_debt_and_it_cannot_come_back(shop: Path) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | |",
    )
    ci(shop, "--update", "--accept-new")
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | F2 |",
    )
    assert ci(shop, "--update").returncode == 0
    assert debt(shop) == []
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | |",
    )
    done = ci(shop)
    assert done.returncode == 1 and "src/billing" in done.stdout


def board(project: Path) -> parch_status.Board:
    blocks = [
        parch_status.Block(
            id=i, title=i, goals=["G1"], depends_on=[], acceptance=[], status="in_progress"
        )
        for i in ("F1", "F2")
    ]
    return parch_status.Board(name="shop", approved=True, criteria={"G1": "цель"}, blocks=blocks)


def test_the_status_board_lists_modules_without_a_block_and_blocks_without_modules(
    shop: Path,
) -> None:
    modules(
        shop,
        "| shop | src/shop | Магазин | Python | active | F1 |",
        "| billing | src/billing | Счета | Python | active | ? |",
    )
    text = "\n".join(parch_status.traceability_lines(shop, board(shop)))
    assert "## Прослеживаемость" in text
    assert "Модулей без блока: 1" in text and "`src/billing`" in text
    assert "Блоков без модулей" in text and "F2" in text


def test_the_status_board_has_no_traceability_without_the_column(tmp_path: Path) -> None:
    write(tmp_path / "docs" / "MODULES.md", "| Модуль | Путь |\n|---|---|\n| shop | src/shop |\n")
    assert parch_status.traceability_lines(tmp_path, board(tmp_path)) == []


def test_the_analyze_draft_has_the_block_column_with_question_marks() -> None:
    from test_analyze_core import analyze

    assert analyze.__name__  # импорт подключает скрипты навыка в sys.path
    mapmod = __import__("analyze_map")
    text = mapmod.modules_markdown(
        [
            {
                "path": "src/shop",
                "languages": {"python": 1},
                "files": 1,
                "test_files": 0,
                "purpose": "x",
                "status": "рабочий",
            }
        ]
    )
    assert "| Статус | Блок |" in text and "| рабочий | ? |" in text
