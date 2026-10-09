"""F25, шаг 2 «Выключатель»: продукт проверяется по тем же правилам, что и проекты пользователей.

Плохие примеры идут через настоящие точки входа продукта на временных проектах: hooks
guard_packages и guard_paths запускаются процессом (run_hook), разбор раздела «Команды проверки»
берётся из stop_gate.configured_commands. Копий логики в тесте нет. Тесты `test_product_*`
применяют те же правила к настоящему репозиторию; они красные, пока в docs/CONSTITUTION.md
продукта нет нужных строк.

Копии `.github/parch/*.py`: у продукта пока нет настоящей проверки на этот случай, поэтому
плохого примера через точку входа нет, остаётся только проверка состояния репозитория (копий
нет). Проверка появится в шаге 3 вместе с шагами CI.
"""

import re
import sys
from pathlib import Path

from conftest import bash, file_call, run_hook

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "hooks"))

from guard_packages import load_allowed, normalize  # type: ignore[import-not-found]  # noqa: E402, I001
from guard_paths import _CODE_ONLY_LINE  # type: ignore[import-not-found]  # noqa: E402, I001
from stop_gate import configured_commands  # type: ignore[import-not-found]  # noqa: E402, I001

EXPECTED_CHECKS = ["ruff check .", "ruff format --check .", "pyright"]
IMPLEMENTER = "parch:implementer"
TESTER = "parch:tester"
CODE_ONLY_YES = "Код правят только роли implementer: да"
PYPROJECT = '[tool.parch]\nsource_roots = ["plugin/hooks", "scripts"]\n'


def make_project(tmp: Path, constitution: str) -> Path:
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / "docs" / "CONSTITUTION.md").write_text(constitution, encoding="utf-8")
    (tmp / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    return tmp


def with_section(title: str, body: str) -> str:
    return f"# C\n\n## {title}\n\n> цитата\n\n{body}\n\n## Другое\n\n- x\n"


def commands_of(root: Path) -> list[str]:
    """Команды проверки так, как их читает настоящий Stop-hook (stop_gate)."""
    return configured_commands(root / "docs" / "CONSTITUTION.md")


def commands_problems(commands: list[str]) -> list[str]:
    """Политика продукта: ровно ruff check, ruff format --check, pyright; без pytest и preflight."""
    if commands == EXPECTED_CHECKS:
        return []
    return [f"команды проверки {commands!r}, нужны {EXPECTED_CHECKS!r}"]


def write_by(root: Path, rel: str, role: str | None = None) -> bool:
    """Настоящий guard_paths: True, если запись заблокирована."""
    return run_hook("guard_paths.py", file_call("Write", root / rel), root, role).blocked


def install_blocked(root: Path, command: str) -> bool:
    """Настоящий guard_packages: True, если установка заблокирована."""
    return run_hook("guard_packages.py", bash(command), root).blocked


def pip_names_of_constitution(root: Path) -> set[str]:
    allowed = load_allowed(root / "docs" / "CONSTITUTION.md")
    return allowed.get("pip", set())


def requirements_names(root: Path) -> set[str]:
    path = root / "requirements-dev.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    names = (re.split(r"[=<>~!\[; ]", x.strip(), maxsplit=1)[0] for x in lines if x.strip())
    return {normalize("pip", n) for n in names if not n.startswith("#")}


# ---- 1. копий .github/parch/*.py нет (точки входа для плохого примера пока нет) ----


def test_product_has_no_copies_of_checks_in_github_parch() -> None:
    folder = REPO / ".github" / "parch"
    assert not folder.is_dir() or sorted(p.name for p in folder.glob("*.py")) == []


# ---- 2. команды проверки (разбор stop_gate.configured_commands) ----


def test_bad_pytest_in_check_commands_is_rejected(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check .\n- pyright\n- pytest -q"
    root = make_project(tmp_path, with_section("Команды проверки", body))
    assert "pytest -q" in commands_of(root)
    assert commands_problems(commands_of(root))


def test_bad_preflight_in_check_commands_is_rejected(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check .\n- pyright\n- python scripts/preflight.py"
    root = make_project(tmp_path, with_section("Команды проверки", body))
    assert "python scripts/preflight.py" in commands_of(root)
    assert commands_problems(commands_of(root))


def test_bad_missing_pyright_in_check_commands_is_rejected(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check ."
    root = make_project(tmp_path, with_section("Команды проверки", body))
    assert commands_of(root) == ["ruff check .", "ruff format --check ."]
    assert commands_problems(commands_of(root))


def test_exactly_three_ruff_and_pyright_commands_are_accepted(tmp_path: Path) -> None:
    body = "- `ruff check .`\n- `ruff format --check .`\n- `pyright`"
    root = make_project(tmp_path, with_section("Команды проверки", body))
    assert commands_problems(commands_of(root)) == []


def test_product_check_commands_are_ruff_format_pyright_only() -> None:
    assert commands_problems(commands_of(REPO)) == []


# ---- 3. код правят только роли implementer (hook guard_paths) ----


def test_bad_main_session_writing_code_is_blocked(tmp_path: Path) -> None:
    root = make_project(tmp_path, f"# C\n\n{CODE_ONLY_YES}\n")
    assert write_by(root, "plugin/hooks/x.py")


def test_bad_main_session_writing_test_is_blocked(tmp_path: Path) -> None:
    root = make_project(tmp_path, f"# C\n\n{CODE_ONLY_YES}\n")
    assert write_by(root, "tests/x.py")


def test_implementer_writes_code_when_rule_is_on(tmp_path: Path) -> None:
    root = make_project(tmp_path, f"# C\n\n{CODE_ONLY_YES}\n")
    assert not write_by(root, "plugin/hooks/x.py", IMPLEMENTER)


def test_tester_writes_test_when_rule_is_on(tmp_path: Path) -> None:
    root = make_project(tmp_path, f"# C\n\n{CODE_ONLY_YES}\n")
    assert not write_by(root, "tests/x.py", TESTER)


def test_code_is_free_when_rule_says_no(tmp_path: Path) -> None:
    root = make_project(tmp_path, "# C\n\nКод правят только роли implementer: нет\n")
    assert not write_by(root, "plugin/hooks/x.py")


def test_code_is_free_without_rule_line(tmp_path: Path) -> None:
    root = make_project(tmp_path, "# C\n\n## Кто правит код\n\nничего\n")
    assert not write_by(root, "plugin/hooks/x.py")


def test_product_code_is_edited_only_by_implementer_roles() -> None:
    text = (REPO / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert _CODE_ONLY_LINE.search(text), "нет строки «Код правят только роли implementer: да»"


# ---- 4. разрешённые пакеты (hook guard_packages) ----

ALLOWED_PIP = with_section("Разрешённые пакеты", "- pip: ruff, pyright")


def test_bad_unlisted_pip_package_install_is_blocked(tmp_path: Path) -> None:
    root = make_project(tmp_path, ALLOWED_PIP)
    assert install_blocked(root, "pip install flask")


def test_bad_unlisted_package_via_uv_add_is_blocked(tmp_path: Path) -> None:
    root = make_project(tmp_path, ALLOWED_PIP)
    assert install_blocked(root, "uv add requests")


def test_listed_pip_package_install_is_allowed(tmp_path: Path) -> None:
    root = make_project(tmp_path, ALLOWED_PIP)
    assert not install_blocked(root, "pip install ruff")


def test_product_allowed_packages_match_requirements_dev() -> None:
    have, want = pip_names_of_constitution(REPO), requirements_names(REPO)
    assert have == want, f"лишние: {sorted(have - want)}, нет: {sorted(want - have)}"
