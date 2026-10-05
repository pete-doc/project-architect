# ruff: noqa: E501
"""F18, PR 2: команда `/parch:trace`: цепочка «цель → блок → спецификация → тесты → модуль».

Плохие примеры: путь вне всех модулей получает ответ «не относится ни к одному блоку» (а не выдуманную цепочку); модуль без
блока и блок, которого нет в `features.json`, названы прямо; из двух вложенных модулей выбирается самый глубокий.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "plugin" / "skills" / "trace" / "scripts" / "trace.py"
SKILL = ROOT / "plugin" / "skills" / "trace" / "SKILL.md"

GOAL = """# GOAL — цель продукта «Магазин»

> **Статус: утверждена владельцем, 2026-10-05.**

## Критерии готовности продукта

- **G1.** Заказ можно оформить за минуту
- **G2.** Цены считаются без ошибок
"""

MODULES = """# MODULES

| Модуль | Путь | Назначение | Язык | Статус | Блок |
|---|---|---|---|---|---|
| shop | src/shop | Магазин | Python | active | F1 |
| pricing | src/shop/pricing | Цены | Python | active | F2 |
| legacy | src/legacy | Старое | Python | active | |
| ghost | src/ghost | Призрак | Python | active | F9 |
"""

CATALOG = """# Каталог возможностей

## Python

| Модуль | Функция | Описание |
|---|---|---|
| `src/shop/orders.py` | `place_order` | Оформляет заказ. |
| `src/shop/pricing/discount.py` | `apply_discount` | Применяет скидку. |
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def project(tmp_path: Path) -> Path:
    write(tmp_path / "docs" / "GOAL.md", GOAL)
    write(tmp_path / "docs" / "MODULES.md", MODULES)
    write(tmp_path / "docs" / "CAPABILITIES.md", CATALOG)
    blocks = [
        {
            "id": "F1",
            "title": "Оформление заказа",
            "goal": ["G1"],
            "spec": "docs/specs/F1.md",
            "acceptance_tests": ["tests/test_orders.py"],
            "status": "done",
        },
        {
            "id": "F2",
            "title": "Цены и скидки",
            "goal": ["G2"],
            "spec": "",
            "acceptance_tests": [],
            "status": "in_progress",
        },
    ]
    write(tmp_path / "state" / "features.json", json.dumps({"version": 1, "features": blocks}))
    return tmp_path


def trace(root: Path, target: str) -> dict[str, Any]:
    done = subprocess.run(
        [sys.executable, str(TRACE)],
        input=json.dumps({"project_dir": str(root), "target": target}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_file_gets_the_chain_goal_block_spec_tests_module(tmp_path: Path) -> None:
    result = trace(project(tmp_path), "src/shop/orders.py")
    assert result["found"] is True and result["module"] == {"path": "src/shop", "status": "active"}
    (block,) = result["blocks"]
    assert (
        block["id"] == "F1" and block["title"] == "Оформление заказа" and block["status"] == "done"
    )
    assert block["spec"] == "docs/specs/F1.md" and block["acceptance_tests"] == [
        "tests/test_orders.py"
    ]
    assert block["goals"] == [{"id": "G1", "text": "Заказ можно оформить за минуту"}]


def test_a_function_name_is_found_through_the_catalog_and_shows_its_description(
    tmp_path: Path,
) -> None:
    result = trace(project(tmp_path), "place_order")
    assert result["found"] is True and result["module"]["path"] == "src/shop"
    assert result["function"] == {
        "file": "src/shop/orders.py",
        "name": "place_order",
        "description": "Оформляет заказ.",
    }


def test_the_deepest_module_wins_and_windows_paths_work(tmp_path: Path) -> None:
    result = trace(project(tmp_path), "src\\shop\\pricing\\discount.py")
    assert result["module"]["path"] == "src/shop/pricing" and result["blocks"][0]["id"] == "F2"
    assert result["blocks"][0]["goals"][0]["id"] == "G2"


def test_a_path_outside_every_module_is_not_given_an_invented_chain(tmp_path: Path) -> None:
    result = trace(project(tmp_path), "scripts/random.py")
    assert result["found"] is False
    assert "не относится ни к одному модулю" in result["message"] and "blocks" not in result


def test_a_module_without_a_block_is_named_a_candidate_for_removal(tmp_path: Path) -> None:
    result = trace(project(tmp_path), "src/legacy/old.py")
    assert result["found"] is True and result["blocks"] == []
    assert "блока у него нет" in result["message"] and "кандидат на удаление" in result["message"]


def test_a_block_missing_from_the_registry_is_reported_as_unknown(tmp_path: Path) -> None:
    result = trace(project(tmp_path), "src/ghost/x.py")
    assert result["found"] is True and result["blocks"][0]["id"] == "F9"
    assert result["blocks"][0]["known"] is False and result["blocks"][0]["goals"] == []


def test_a_missing_target_is_an_error_not_a_guess(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(TRACE)],
        input=json.dumps({"project_dir": str(project(tmp_path))}),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert done.returncode == 1 and "нужен target" in done.stderr


def test_the_skill_asks_for_a_plain_russian_answer_and_never_invents_a_chain() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: trace\n")
    assert "по-русски" in text and "Ничего не выдумывай" in text and "trace.py" in text
