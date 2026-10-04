# ruff: noqa: E501
"""F15, PR 3: каталог возможностей и обязательные описания публичных функций (Python и C#).

Плохие примеры, которые проверка обязана остановить: публичная функция без описания, описание длиннее предела,
каталог отстал от кода, каталога нет. Закрытые функции, тесты и сборочные папки в каталог не попадают.
"""

import subprocess
import sys
from pathlib import Path

import parch_catalog
import pytest

CI = Path(__file__).resolve().parents[1] / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

PY_OK = '''"""Модуль заказов."""


def total(items: list[float]) -> float:
    """Считает сумму заказа без скидки."""
    return sum(items)


def _helper() -> None:
    return None


class Order:
    """Заказ."""

    def add(self, price: float) -> None:
        """Добавляет позицию в заказ.

        Подробности для читателя, в каталог не попадают.
        """

    def _private(self) -> None:
        return None


class _Hidden:
    def go(self) -> None:
        return None
'''

CS_OK = """namespace Shop;

public class Pricing
{
    /// <summary>Считает скидку для заказа.</summary>
    public decimal Discount(decimal total) => total;

    private void Hidden() { }
}

internal class Inner
{
    public void NotPublicType() { }
}
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ci(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CI), "catalog", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    write(tmp_path / "src" / "shop" / "orders.py", PY_OK)
    return tmp_path


def test_the_catalog_lists_only_public_functions_with_their_one_line_descriptions(
    py_project: Path,
) -> None:
    entries, missing = parch_catalog.collect(py_project)
    assert missing == []
    assert [(e.name, e.description) for e in entries] == [
        ("total", "Считает сумму заказа без скидки."),
        ("Order.add", "Добавляет позицию в заказ."),
    ]


def test_update_builds_the_catalog_and_then_the_check_passes(py_project: Path) -> None:
    assert ci(py_project).returncode == 1  # каталога ещё нет
    assert ci(py_project, "--update").returncode == 0
    text = (py_project / "docs" / "CAPABILITIES.md").read_text(encoding="utf-8")
    assert "| `src/shop/orders.py` | `total` | Считает сумму заказа без скидки. |" in text
    assert "_helper" not in text and "_private" not in text and "Hidden" not in text
    done = ci(py_project)
    assert done.returncode == 0, done.stdout


def test_a_public_function_without_a_description_fails_and_update_does_not_hide_it(
    py_project: Path,
) -> None:
    ci(py_project, "--update")
    write(py_project / "src" / "shop" / "extra.py", "def forgotten() -> int:\n    return 1\n")
    done = ci(py_project)
    assert done.returncode == 1 and "src/shop/extra.py:1: forgotten: нет описания" in done.stdout
    assert ci(py_project, "--update").returncode == 1  # описание не придумывается за автора


def test_a_description_longer_than_the_limit_fails(py_project: Path) -> None:
    long = "а" * 130
    write(py_project / "src" / "shop" / "long.py", f'def wordy() -> None:\n    """{long}"""\n')
    done = ci(py_project)
    assert done.returncode == 1 and "длиннее" in done.stdout


def test_a_stale_catalog_fails_when_the_code_changes(py_project: Path) -> None:
    ci(py_project, "--update")
    write(
        py_project / "src" / "shop" / "more.py",
        'def fresh() -> None:\n    """Новая возможность."""\n',
    )
    done = ci(py_project)
    assert done.returncode == 1 and "отстал от кода" in done.stdout
    ci(py_project, "--update")
    assert ci(py_project).returncode == 0


def test_a_hand_edited_catalog_is_detected_as_stale(py_project: Path) -> None:
    ci(py_project, "--update")
    catalog = py_project / "docs" / "CAPABILITIES.md"
    catalog.write_text(
        catalog.read_text(encoding="utf-8") + "| лишнее | вручную | дописано |\n", encoding="utf-8"
    )
    assert ci(py_project).returncode == 1


def test_tests_and_build_folders_are_not_part_of_the_catalog(py_project: Path) -> None:
    write(py_project / "tests" / "test_orders.py", "def test_total() -> None:\n    pass\n")
    write(py_project / "build" / "gen.py", "def generated() -> None:\n    pass\n")
    write(py_project / "src" / "shop" / "test_like.py", "def not_a_test_dir() -> None:\n    pass\n")
    write(py_project / "parch-analysis" / "x.py", "def report() -> None:\n    pass\n")
    entries, missing = parch_catalog.collect(py_project)
    assert missing == [] and [e.name for e in entries] == ["total", "Order.add"]


def test_a_project_without_public_functions_needs_no_catalog(tmp_path: Path) -> None:
    write(tmp_path / "src" / "a.py", "def _private() -> None:\n    pass\n")
    done = ci(tmp_path)
    assert done.returncode == 0, done.stdout


def test_csharp_public_methods_of_public_types_need_a_summary(tmp_path: Path) -> None:
    write(tmp_path / "src" / "Pricing.cs", CS_OK)
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == []
    assert [(e.name, e.description) for e in entries] == [
        ("Pricing.Discount", "Считает скидку для заказа.")
    ]
    assert ci(tmp_path, "--update").returncode == 0
    assert "| `src/Pricing.cs` | `Pricing.Discount` |" in (
        tmp_path / "docs" / "CAPABILITIES.md"
    ).read_text(encoding="utf-8")
    assert ci(tmp_path).returncode == 0


def test_a_csharp_public_method_without_a_summary_fails(tmp_path: Path) -> None:
    write(
        tmp_path / "src" / "Bad.cs",
        "public class Bad\n{\n    public int Count()\n    {\n        return 1;\n    }\n}\n",
    )
    done = ci(tmp_path)
    assert done.returncode == 1 and "src/Bad.cs:3: Bad.Count: нет описания" in done.stdout


def test_csharp_brace_on_the_next_line_and_inheritdoc_and_test_projects(tmp_path: Path) -> None:
    write(
        tmp_path / "src" / "Svc.cs",
        "public class Svc : Base\n{\n    /// <inheritdoc />\n    public override void Run() { }\n\n"
        "    /// <summary>\n    /// Делает работу.\n    /// Подробности.\n    /// </summary>\n"
        "    [Obsolete]\n    public void Work() { }\n}\n",
    )
    write(
        tmp_path / "tests" / "Shop.Tests" / "T.cs",
        "public class T\n{\n    public void Bad() { }\n}\n",
    )
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == [] and [(e.name, e.description) for e in entries] == [
        ("Svc.Work", "Делает работу. Подробности.")
    ]
