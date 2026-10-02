"""CI для Python: тестовый проект с намеренными нарушениями и «храповик».

Каждое нарушение (дубль, мёртвая функция, запрещённая зависимость между модулями, удалённый
тест, пакет не из списка, модуль вне MODULES.md) должно быть поймано проверкой CI. Отдельно
проверяется, что «храповик» пропускает старые нарушения и блокирует новые.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SHOP = REPO / "tests" / "projects" / "python_shop"
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
JSCPD_VERSION = "5.4.0"

DUPLICATE_BODY = """

def summary_{n}(prices: list[float], discount: float) -> str:
    lines: list[str] = []
    total = 0.0
    for index, price in enumerate(prices):
        shown = round(price * (1 - discount), 2)
        total += shown
        lines.append(f"{{index + 1}}. {{shown:.2f}}")
    lines.append(f"Total: {{total:.2f}}")
    lines.append("Thanks for the order")
    return "\\n".join(lines)
"""
OTHER_DUPLICATE_BODY = """

def report_{n}(items: dict[str, int], limit: int) -> list[str]:
    rows: list[str] = []
    seen = 0
    for name, count in sorted(items.items()):
        if count > limit:
            continue
        seen += count
        rows.append(name.upper() + ":" + str(count * 2))
    rows.append("seen=" + str(seen))
    rows.append("limit=" + str(limit))
    return rows
"""


@pytest.fixture(scope="session")
def jscpd(tmp_path_factory: pytest.TempPathFactory) -> str:
    """jscpd той же версии, что в CI-шаблоне, установленный во временную папку."""
    folder = tmp_path_factory.mktemp("jscpd")
    node = shutil.which("node")
    if node is None:
        pytest.skip("нужен Node.js для jscpd")
    npm_cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm = [node, str(npm_cli)] if npm_cli.is_file() else [shutil.which("npm") or "npm"]
    (folder / "package.json").write_text('{"private": true}', encoding="utf-8")
    done = subprocess.run(
        [*npm, "install", f"jscpd@{JSCPD_VERSION}", "--no-audit", "--no-fund"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    entry = folder / "node_modules" / "jscpd" / "run-jscpd.js"
    return f'"{node}" "{entry}"'.replace("\\", "/")


class Shop:
    """Копия тестового проекта и запуск проверок CI так, как их запускает workflow."""

    def __init__(self, root: Path, jscpd_cmd: str) -> None:
        self.root = root
        self.env = {
            **os.environ,
            "PARCH_JSCPD": jscpd_cmd,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        }

    def run(self, check: str, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), check, "--project", str(self.root), *flags],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=False,
            timeout=600,
        )

    def passes(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
        assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
        return done.stdout

    def fails(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
        assert done.returncode == 1, f"{check} должен был упасть: {done.stdout}{done.stderr}"
        assert "ПРОВАЛ" in done.stdout
        return done.stdout

    def write(self, rel: str, text: str) -> None:
        (self.root / rel).write_text(text, encoding="utf-8", newline="\n")

    def append(self, rel: str, text: str) -> None:
        with (self.root / rel).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def all_checks(self) -> dict[str, int]:
        names = ["tests", "modules", "deps", "dead-code", "duplicates", "architecture", "coverage"]
        return {name: self.run(name).returncode for name in names}


@pytest.fixture
def make_shop(tmp_path: Path, jscpd: str) -> Callable[[], Shop]:
    def build() -> Shop:
        root = tmp_path / "shop"
        shutil.copytree(
            SHOP, root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "state")
        )
        (root / "state").mkdir(exist_ok=True)
        return Shop(root, jscpd)

    return build


@pytest.fixture
def shop(make_shop: Callable[[], Shop]) -> Iterator[Shop]:
    """Чистый проект с записанным baseline."""
    project = make_shop()
    out = project.passes("baseline", "--update")
    assert "baseline обновлён" in out
    yield project


def test_clean_project_passes_every_check(shop: Shop) -> None:
    assert shop.all_checks() == dict.fromkeys(
        ["tests", "modules", "deps", "dead-code", "duplicates", "architecture", "coverage"], 0
    )


# ---------- каждое намеренное нарушение ловится ----------


def test_duplicate_code_is_caught(shop: Shop) -> None:
    shop.append("src/shop/pricing.py", DUPLICATE_BODY.format(n=1))
    shop.append("src/shop/orders.py", DUPLICATE_BODY.format(n=2))
    out = shop.fails("duplicates")
    assert "новые дубли" in out


def test_dead_function_is_caught(shop: Shop) -> None:
    shop.append("src/shop/util.py", "\n\ndef never_called() -> int:\n    return 42\n")
    out = shop.fails("dead-code")
    assert "never_called" in out
    assert "неиспользуемый function" in out


def test_forbidden_dependency_between_modules_is_caught(shop: Shop) -> None:
    shop.write(
        "src/shop/ui.py",
        '"""Текстовый интерфейс."""\n\nfrom shop import db, orders\n\n\n'
        "def show(order_id: int) -> str:\n"
        '    return f"Заказ {order_id}: {db.load(order_id):.2f} {orders.total_of(order_id)}"\n',
    )
    out = shop.fails("architecture")
    assert "нарушены" in out
    assert "BROKEN" in out or "broken" in out


def test_deleted_test_is_caught(shop: Shop) -> None:
    (shop.root / "tests" / "test_pricing.py").unlink()
    out = shop.fails("tests")
    assert "Число тестов уменьшилось" in out
    assert "tests/test_pricing.py::test_discount_is_applied" in out


def test_one_deleted_test_function_is_caught_even_if_another_is_added(shop: Shop) -> None:
    shop.write(
        "tests/test_pricing.py",
        "from shop import pricing\n\n\n"
        "def test_discount_is_applied() -> None:\n"
        "    assert pricing.apply_discount(100.0, 0.25) == 75.0\n\n\n"
        "def test_something_new() -> None:\n    assert True\n",
    )
    out = shop.fails("tests")  # число тестов то же, но один из прежних исчез
    assert "test_discount_is_capped_at_ninety_percent" in out


@pytest.mark.parametrize(
    ("file", "content"),
    [
        ("requirements.txt", "flask==3.1.0\n"),
        ("requirements-dev.txt", "ruff==0.16.10\nsomething-new==1.0\n"),
        ("pyproject.toml", '[project]\nname = "shop"\nversion = "0"\ndependencies = ["django"]\n'),
        ("requirements.txt", "-r other.txt\n"),
        ("requirements.txt", "git+https://github.com/x/y.git\n"),
    ],
)
def test_package_outside_the_allowed_list_is_caught(shop: Shop, file: str, content: str) -> None:
    if file == "requirements.txt" and content.startswith("-r"):
        shop.write("other.txt", "flask\n")
    if file == "pyproject.toml":
        content = (shop.root / file).read_text(encoding="utf-8") + "\n" + content
    shop.append(file, content) if file != "pyproject.toml" else shop.write(file, content)
    out = shop.fails("deps")
    assert "Разрешённые пакеты" in out


def test_listed_package_is_accepted(shop: Shop) -> None:
    shop.write("requirements.txt", "Requests>=2\npytest-cov\n")
    shop.passes("deps")


def test_module_missing_from_modules_md_is_caught(shop: Shop) -> None:
    (shop.root / "src" / "billing").mkdir()
    shop.write("src/billing/__init__.py", '"""Счета."""\n')
    out = shop.fails("modules")
    assert "src/billing" in out


def test_module_outside_the_architecture_rules_is_caught(shop: Shop) -> None:
    (shop.root / "src" / "billing").mkdir()
    shop.write("src/billing/__init__.py", '"""Счета."""\n')
    shop.append("docs/MODULES.md", "| billing | src/billing | Счета | Python | active |\n")
    shop.passes("modules")
    out = shop.fails("architecture")
    assert "billing" in out
    assert "не охвачены" in out


# ---------- правило архитектуры не бывает «пусто-зелёным» ----------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("forbidden_modules = shop.db", "forbidden_modules = shop.dbb"),
        ("source_modules = shop.ui", "source_modules = shop.uii"),
    ],
)
def test_architecture_rule_with_a_typo_fails_instead_of_passing(
    shop: Shop, old: str, new: str
) -> None:
    config = (shop.root / ".importlinter").read_text(encoding="utf-8")
    shop.write(".importlinter", config.replace(old, new))
    shop.fails("architecture")


def test_architecture_without_contracts_or_config_fails(shop: Shop) -> None:
    shop.write(".importlinter", "[importlinter]\nroot_package = shop\n")
    assert "ни одного контракта" in shop.fails("architecture")
    (shop.root / ".importlinter").unlink()
    assert "нет правил архитектуры" in shop.fails("architecture")


def test_project_without_tests_is_not_green(shop: Shop) -> None:
    shutil.rmtree(shop.root / "tests")
    (shop.root / "tests").mkdir()
    assert "нет ни одного теста" in shop.fails("tests")


# ---------- «храповик» ----------


@pytest.fixture
def legacy(make_shop: Callable[[], Shop]) -> Shop:
    """Проект со старыми нарушениями (мёртвая функция, дубль), записанными в baseline."""
    project = make_shop()
    project.append("src/shop/util.py", "\n\ndef legacy_unused() -> int:\n    return 1\n")
    project.append("src/shop/pricing.py", DUPLICATE_BODY.format(n=1))
    project.append("src/shop/orders.py", DUPLICATE_BODY.format(n=2))
    project.append(
        "tests/test_pricing.py", "\n\ndef test_uses_legacy() -> None:\n    assert True\n"
    )
    project.passes("baseline", "--update")
    return project


def test_ratchet_lets_old_violations_through(legacy: Shop) -> None:
    out = legacy.passes("dead-code")
    assert "новых нет" in out
    legacy.passes("duplicates")
    assert set(legacy.all_checks().values()) == {0}


def test_ratchet_blocks_a_new_dead_function_but_not_the_old_one(legacy: Shop) -> None:
    legacy.append("src/shop/util.py", "\n\ndef brand_new_unused() -> int:\n    return 2\n")
    out = legacy.fails("dead-code")
    assert "brand_new_unused" in out
    assert "legacy_unused" not in out


def test_ratchet_blocks_a_new_duplicate_but_not_the_old_one(legacy: Shop) -> None:
    legacy.append("src/shop/util.py", OTHER_DUPLICATE_BODY.format(n=1))
    legacy.append("src/shop/db.py", OTHER_DUPLICATE_BODY.format(n=2))
    out = legacy.fails("duplicates")
    assert "новые дубли" in out
    assert "Found 2 clones (1 new)" in out or "1 new" in out


def test_ratchet_notices_improvement_and_keeps_the_gain(legacy: Shop) -> None:
    source = (legacy.root / "src" / "shop" / "util.py").read_text(encoding="utf-8")
    legacy.write(
        "src/shop/util.py", source.replace("\n\ndef legacy_unused() -> int:\n    return 1\n", "")
    )
    out = legacy.passes("dead-code")
    assert "Улучшение" in out
    legacy.passes("baseline", "--update")
    legacy.append("src/shop/util.py", "\n\ndef legacy_unused() -> int:\n    return 1\n")
    legacy.fails("dead-code")  # старое нарушение вернулось: теперь оно новое


def test_baseline_update_refuses_new_violations_and_removed_tests(legacy: Shop) -> None:
    legacy.append("src/shop/util.py", "\n\ndef sneaky_unused() -> int:\n    return 3\n")
    refused = legacy.fails("baseline", "--update")
    assert "sneaky_unused" in refused
    assert "--accept-new" in refused
    legacy.passes("baseline", "--update", "--accept-new")
    legacy.passes("dead-code")
    (legacy.root / "tests" / "test_pricing.py").unlink()
    refused_tests = legacy.fails("baseline", "--update", "--accept-new")
    assert "--accept-removed" in refused_tests
    legacy.passes("baseline", "--update", "--accept-removed")


def test_coverage_ratchet_blocks_a_drop(shop: Shop) -> None:
    baseline = json.loads((shop.root / "state" / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["coverage"]["python"] == 100.0
    shop.append(
        "src/shop/pricing.py",
        "\n\ndef untested_helper(value: float) -> float:\n"
        "    if value > 1:\n        return value\n    return 0.0\n",
    )
    shop.append(
        "tests/test_pricing.py",
        "\n\ndef test_touch() -> None:\n    assert pricing.apply_discount(1.0, 0.0) == 1.0\n",
    )
    shop.append(
        "src/shop/orders.py",
        "\n\ndef use_helper() -> float:\n    return pricing.untested_helper(2.0)\n",
    )
    out = shop.fails("coverage")
    assert "Покрытие упало" in out


def test_baseline_file_is_stable_and_machine_independent(shop: Shop) -> None:
    first = (shop.root / "state" / "baseline.json").read_text(encoding="utf-8")
    shop.passes("baseline", "--update")
    assert (shop.root / "state" / "baseline.json").read_text(encoding="utf-8") == first
    data = json.loads(first)
    assert data["tests"]["python"][0].startswith("tests/")  # пути с прямыми слэшами
    assert "\\" not in first.replace("\\n", "")


# ---------- CI-шаблон и версии инструментов ----------


def test_workflow_template_runs_every_check_and_pins_versions() -> None:
    workflow = (REPO / "plugin" / "templates" / "ci" / "python.yml").read_text(encoding="utf-8")
    for command in (
        "ruff check .",
        "ruff format --check .",
        "pyright",
        "pytest -v",
        "parch_ci.py tests",
        "parch_ci.py modules",
        "parch_ci.py deps",
        "parch_ci.py architecture",
        "parch_ci.py dead-code",
        "parch_ci.py duplicates",
        "parch_ci.py coverage",
    ):
        assert command in workflow, command
    assert "ubuntu-latest" in workflow and "windows-latest" in workflow
    assert 'node-version: "22"' in workflow  # jscpd запускается через npx из скрипта
    script = SCRIPT.read_text(encoding="utf-8")
    assert f'JSCPD_VERSION = "{JSCPD_VERSION}"' in script  # версия jscpd зафиксирована


# ---------- пропущенные тесты считаются удалёнными ----------

SKIP_VARIANTS = [
    '@pytest.mark.skip(reason="потом")\ndef test_extra() -> None:\n    assert True\n',
    '@pytest.mark.skipif(True, reason="потом")\ndef test_extra() -> None:\n    assert True\n',
    "@pytest.mark.xfail\ndef test_extra() -> None:\n    assert False\n",
    'def test_extra() -> None:\n    pytest.skip("потом")\n',
    'def test_extra() -> None:\n    pytest.importorskip("nonexistent_module")\n',
    'pytestmark = pytest.mark.skip(reason="всё")\n\n\ndef test_extra() -> None:\n    assert True\n',
    "@unittest.skip('потом')\ndef test_extra() -> None:\n    assert True\n",
    "class TestOld(unittest.TestCase):\n    def test_old(self) -> None:\n"
    "        self.skipTest('потом')\n",
]


def add_extra_test(shop: Shop, body: str) -> None:
    imports = [name for name in ("unittest", "pytest") if name in body]
    header = "".join(f"import {name}\n" for name in imports)
    shop.write("tests/test_extra.py", header + ("\n\n" if header else "") + body)


@pytest.mark.parametrize("body", SKIP_VARIANTS)
def test_a_new_skipped_test_is_a_violation(shop: Shop, body: str) -> None:
    add_extra_test(shop, body)
    out = shop.fails("skips")
    assert "tests/test_extra.py" in out
    assert "Пропущенный тест не проверяет ничего" in out
    assert "--accept-skips" in out


def test_skip_words_in_strings_and_comments_are_not_skips(shop: Shop) -> None:
    add_extra_test(
        shop,
        '# @pytest.mark.skip(reason="закомментировано")\n'
        'TEXT = "pytest.skip(1) и @pytest.mark.xfail"\n\n\n'
        "def test_extra() -> None:\n    assert TEXT\n",
    )
    shop.passes("skips")


def test_old_skips_pass_and_new_ones_do_not(make_shop: Callable[[], Shop]) -> None:
    project = make_shop()
    add_extra_test(project, SKIP_VARIANTS[0])
    project.passes("baseline", "--update")
    project.passes("skips")
    project.append("tests/test_extra.py", "\n\n" + SKIP_VARIANTS[1].split("\n\n", 0)[0])
    out = project.fails("skips")
    assert "было 1, стало 2" in out or "было 1, стало" in out


def test_baseline_refuses_new_skips_without_the_owner_flag(shop: Shop) -> None:
    add_extra_test(shop, SKIP_VARIANTS[0])
    refused = shop.fails("baseline", "--update")
    assert "--accept-skips" in refused
    shop.passes("baseline", "--update", "--accept-skips")
    shop.passes("skips")


def test_removing_a_skip_is_an_improvement(make_shop: Callable[[], Shop]) -> None:
    project = make_shop()
    add_extra_test(project, SKIP_VARIANTS[0])
    project.passes("baseline", "--update")
    add_extra_test(project, "def test_extra() -> None:\n    assert True\n")
    assert "Улучшение" in project.passes("skips")


# ---------- подавляющие комментарии: число не растёт ----------

SUPPRESSIONS = [
    "x: int = 'a'  # type: ignore",
    "x: int = 'a'  # pyright: ignore[reportAssignmentType]",
    "import os  # noqa: F401",
    "import sys  # ruff: noqa",
    "def f() -> None:  # pragma: no cover\n    pass",
    "# pyright: basic",
    "# mypy: ignore-errors",
]


@pytest.mark.parametrize("line", SUPPRESSIONS)
def test_a_new_suppression_comment_is_a_violation(shop: Shop, line: str) -> None:
    shop.append("src/shop/util.py", "\n\n" + line + "\n")
    out = shop.fails("suppressions")
    assert "src/shop/util.py" in out
    assert "Число подавлений не должно расти" in out
    assert "--accept-suppressions" in out


def test_suppression_words_in_strings_are_not_suppressions(shop: Shop) -> None:
    shop.append("src/shop/util.py", '\n\nNOTE = "# noqa и # type: ignore как текст"\n')
    shop.passes("suppressions")


def test_old_suppressions_pass_new_ones_do_not_and_moving_inside_a_file_is_fine(
    make_shop: Callable[[], Shop],
) -> None:
    project = make_shop()
    project.append("src/shop/util.py", "\n\nimport os  # noqa: F401\n")
    project.passes("baseline", "--update")
    project.passes("suppressions")
    util = (project.root / "src" / "shop" / "util.py").read_text(encoding="utf-8")
    moved = util.replace("\n\nimport os  # noqa: F401\n", "") + "\n\nimport sys  # noqa: F401\n"
    project.write("src/shop/util.py", moved)
    project.passes("suppressions")  # то же число в том же файле
    project.append("src/shop/util.py", "\n\nimport re  # noqa: F401\n")
    assert "было 1, стало 2" in project.fails("suppressions")


def test_baseline_refuses_new_suppressions_without_the_owner_flag(shop: Shop) -> None:
    shop.append("src/shop/util.py", "\n\nimport os  # noqa: F401\n")
    refused = shop.fails("baseline", "--update")
    assert "--accept-suppressions" in refused
    shop.passes("baseline", "--update", "--accept-suppressions")
    shop.passes("suppressions")


# ---------- настройки проверок защищены отпечатком ----------


def edit_settings(shop: Shop, rel: str, old: str, new: str) -> None:
    text = (shop.root / rel).read_text(encoding="utf-8")
    assert old in text, (rel, old)
    shop.write(rel, text.replace(old, new, 1))


SETTINGS_CHANGES = [
    ("pyproject.toml", "line-length = 100", "line-length = 200"),
    ("pyproject.toml", 'select = ["E", "F", "I", "B", "UP"]', 'select = ["E"]'),
    (
        "pyproject.toml",
        'testpaths = ["tests"]',
        'testpaths = ["tests"]\naddopts = "--deselect tests"',
    ),
    (
        ".importlinter",
        "allow_indirect_imports = True",
        "allow_indirect_imports = True\nignore_imports =\n    shop.ui -> shop.db",
    ),
]


@pytest.mark.parametrize(("rel", "old", "new"), SETTINGS_CHANGES)
def test_changing_check_settings_is_blocked(shop: Shop, rel: str, old: str, new: str) -> None:
    edit_settings(shop, rel, old, new)
    out = shop.fails("settings")
    assert rel in out
    assert "--accept-config" in out


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("ruff.toml", "line-length = 300\n"),
        ("pyrightconfig.json", '{"typeCheckingMode": "off"}\n'),
        (".coveragerc", "[run]\nomit = src/*\n"),
        ("pytest.ini", "[pytest]\naddopts = -k nothing\n"),
        ("setup.cfg", "[tool:pytest]\naddopts = --co\n"),
        ("tox.ini", "[pytest]\naddopts = -q\n"),
    ],
)
def test_adding_a_settings_file_is_blocked(shop: Shop, rel: str, content: str) -> None:
    shop.write(rel, content)
    out = shop.fails("settings")
    assert rel in out


def test_deleting_the_architecture_config_is_blocked(shop: Shop) -> None:
    (shop.root / ".importlinter").unlink()
    out = shop.fails("settings")
    assert ".importlinter: удалён" in out


def test_unrelated_changes_do_not_touch_the_settings_fingerprint(shop: Shop) -> None:
    text = (shop.root / "pyproject.toml").read_text(encoding="utf-8")
    shop.write("pyproject.toml", text + '\n[project]\nname = "shop"\nversion = "1"\n')
    shop.passes("settings")


def test_baseline_refuses_changed_settings_without_the_owner_flag(shop: Shop) -> None:
    edit_settings(shop, "pyproject.toml", "line-length = 100", "line-length = 120")
    refused = shop.fails("baseline", "--update")
    assert "--accept-config" in refused
    shop.passes("baseline", "--update", "--accept-config")
    shop.passes("settings")


def test_workflow_template_runs_the_new_checks() -> None:
    workflow = (REPO / "plugin" / "templates" / "ci" / "python.yml").read_text(encoding="utf-8")
    for check in ("skips", "suppressions", "settings"):
        assert f"parch_ci.py {check}" in workflow


# ---------- пропуски по фактическому результату запуска: псевдонимы не обойти ----------

ALIAS_VARIANTS = [
    "import pytest as pt\n\n\n@pt.mark.skip(reason='x')\n"
    "def test_extra() -> None:\n    assert True\n",
    "from pytest import mark\n\n\n@mark.skip(reason='x')\n"
    "def test_extra() -> None:\n    assert True\n",
    "from pytest import mark as m\n\n\n@m.xfail\ndef test_extra() -> None:\n    assert False\n",
    "from pytest import skip as bail\n\n\ndef test_extra() -> None:\n    bail('x')\n",
    "import pytest as p\n\n\ndef test_extra() -> None:\n    p.skip('x')\n",
    "from pytest import importorskip as need\n\n\n"
    "def test_extra() -> None:\n    need('nonexistent_module_xyz')\n",
]
EXTRA_ID = "tests.test_extra::test_extra"


@pytest.mark.parametrize("body", ALIAS_VARIANTS)
def test_aliased_skips_are_caught_by_the_actual_test_run(shop: Shop, body: str) -> None:
    shop.write("tests/test_extra.py", body)
    out = shop.fails("skips")
    assert "по фактическому результату запуска" in out
    assert EXTRA_ID in out
    assert "псевдонимы" in out


def test_text_search_alone_would_miss_a_renamed_skip_function(shop: Shop) -> None:
    """Текстовый поиск оставлен дополнительным: он слеп к `skip as bail`, запуск нет."""
    from parch_ci import RULES, scan_counts

    shop.write("tests/test_extra.py", ALIAS_VARIANTS[3])
    seen = scan_counts(shop.root, "python", RULES["python"].skip_patterns, True)
    assert not any("test_extra.py" in key for key in seen)
    assert EXTRA_ID in shop.fails("skips")


def test_text_search_still_runs_next_to_the_test_report(shop: Shop) -> None:
    shop.write("tests/test_extra.py", ALIAS_VARIANTS[0])
    out = shop.fails("skips")
    assert "Пропуски в тексте тестов" in out  # дополнительная проверка по тексту (mark.skip)
    assert "Пропущено по фактическому результату запуска" in out


def test_aliased_skip_in_baseline_passes_and_a_new_one_does_not(
    make_shop: Callable[[], Shop],
) -> None:
    project = make_shop()
    project.write("tests/test_extra.py", ALIAS_VARIANTS[3])
    project.passes("baseline", "--update")
    assert "все известны" in project.passes("skips")
    project.append("tests/test_extra.py", "\n\ndef test_second() -> None:\n    bail('y')\n")
    out = project.fails("skips")
    assert "tests.test_extra::test_second" in out
    assert "tests.test_extra::test_extra" not in out.split("новых")[1]


def test_baseline_refuses_actual_skips_without_the_owner_flag(shop: Shop) -> None:
    shop.write("tests/test_extra.py", ALIAS_VARIANTS[1])
    refused = shop.fails("baseline", "--update")
    assert "--accept-skips" in refused
    assert EXTRA_ID in refused
    shop.passes("baseline", "--update", "--accept-skips")
    shop.passes("skips")


def test_skip_decided_at_run_time_by_a_condition_is_caught(shop: Shop) -> None:
    shop.write(
        "tests/test_extra.py",
        "import sys\n\nimport pytest\n\n\n"
        "@pytest.mark.skipif(sys.version_info >= (3, 0), reason='всегда')\n"
        "def test_extra() -> None:\n    assert True\n",
    )
    assert EXTRA_ID in shop.fails("skips")
