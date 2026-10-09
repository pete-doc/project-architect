"""F25, шаг 2 «Выключатель»: продукт проверяется по тем же правилам, что и проекты пользователей.

Каждая проверка оформлена помощником над корнем проекта; у каждого есть плохой пример на временной
папке и применение к настоящему репозиторию. Настоящие проверки красные, пока в docs/CONSTITUTION.md
продукта нет нужных строк.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "hooks"))

from guard_paths import _CODE_ONLY_LINE  # type: ignore[import-not-found]  # noqa: E402, I001

EXPECTED_CHECKS = ["ruff check .", "ruff format --check .", "pyright"]


def constitution_text(root: Path) -> str:
    path = root / "docs" / "CONSTITUTION.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def section_items(text: str, title: str) -> list[str]:
    """Строки `- ...` раздела `## title` (цитаты `>` и пустые строки пропускаются)."""
    items: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == title
            continue
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if inside and match:
            items.append(match.group(1).strip("`").strip())
    return items


def shell_copies(root: Path) -> list[str]:
    folder = root / ".github" / "parch"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob("*.py"))


def check_commands_problems(root: Path) -> list[str]:
    commands = section_items(constitution_text(root), "Команды проверки")
    if commands == EXPECTED_CHECKS:
        return []
    return [f"команды проверки {commands!r}, нужны {EXPECTED_CHECKS!r}"]


def code_only_problems(root: Path) -> list[str]:
    if _CODE_ONLY_LINE.search(constitution_text(root)):
        return []
    return ["нет строки «Код правят только роли implementer: да»"]


def pip_names_from_constitution(root: Path) -> set[str]:
    names: set[str] = set()
    for item in section_items(constitution_text(root), "Разрешённые пакеты"):
        eco, _, rest = item.partition(":")
        if eco.strip() == "pip":
            names |= {_norm(n) for n in rest.split(",") if n.strip()}
    return names


def _norm(name: str) -> str:
    return re.split(r"[=<>~!\[; ]", name.strip(), maxsplit=1)[0].lower().replace("_", "-")


def requirements_names(root: Path) -> set[str]:
    path = root / "requirements-dev.txt"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return {_norm(x) for x in lines if x.strip() and not x.lstrip().startswith("#")}


def packages_problems(root: Path) -> list[str]:
    have, want = pip_names_from_constitution(root), requirements_names(root)
    if have == want:
        return []
    return [f"лишние: {sorted(have - want)}, нет: {sorted(want - have)}"]


def make_root(
    tmp: Path, constitution: str, requirements: str = "ruff==1.0\npyright==2.0\n"
) -> Path:
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / "docs" / "CONSTITUTION.md").write_text(constitution, encoding="utf-8")
    (tmp / "requirements-dev.txt").write_text(requirements, encoding="utf-8")
    return tmp


def with_section(title: str, body: str) -> str:
    return f"# C\n\n## {title}\n\n> цитата\n\n{body}\n\n## Другое\n\n- x\n"


# ---- 1. копий .github/parch/*.py нет ----


def test_bad_copy_of_parch_ci_in_github_is_found(tmp_path: Path) -> None:
    (tmp_path / ".github" / "parch").mkdir(parents=True)
    (tmp_path / ".github" / "parch" / "parch_ci.py").write_text("", encoding="utf-8")
    assert shell_copies(tmp_path) == ["parch_ci.py"]


def test_no_github_parch_folder_or_empty_one_is_clean(tmp_path: Path) -> None:
    assert shell_copies(tmp_path) == []
    (tmp_path / ".github" / "parch").mkdir(parents=True)
    assert shell_copies(tmp_path) == []


def test_product_has_no_copies_of_checks_in_github_parch() -> None:
    assert shell_copies(REPO) == []


# ---- 2. команды проверки ----


def test_bad_pytest_in_check_commands_is_rejected(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check .\n- pyright\n- pytest -q"
    root = make_root(tmp_path, with_section("Команды проверки", body))
    assert check_commands_problems(root)


def test_bad_preflight_in_check_commands_is_rejected(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check .\n- pyright\n- python scripts/preflight.py"
    root = make_root(tmp_path, with_section("Команды проверки", body))
    assert check_commands_problems(root)


def test_bad_missing_pyright_in_check_commands_is_rejected(tmp_path: Path) -> None:
    root = make_root(
        tmp_path, with_section("Команды проверки", "- ruff check .\n- ruff format --check .")
    )
    assert check_commands_problems(root)


def test_exactly_three_ruff_and_pyright_commands_are_accepted(tmp_path: Path) -> None:
    body = "- ruff check .\n- ruff format --check .\n- pyright"
    root = make_root(tmp_path, with_section("Команды проверки", body))
    assert check_commands_problems(root) == []


def test_product_check_commands_are_ruff_format_pyright_only() -> None:
    assert check_commands_problems(REPO) == []


# ---- 3. код правят только роли implementer ----


def test_bad_code_only_line_missing_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path, "# C\n\n## Кто правит код\n\nничего\n")
    assert code_only_problems(root)


def test_bad_code_only_line_set_to_no_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path, "# C\n\nКод правят только роли implementer: нет\n")
    assert code_only_problems(root)


def test_code_only_line_yes_is_accepted(tmp_path: Path) -> None:
    root = make_root(tmp_path, "# C\n\nКод правят только роли implementer: да\n")
    assert code_only_problems(root) == []


def test_product_code_is_edited_only_by_implementer_roles() -> None:
    assert code_only_problems(REPO) == []


# ---- 4. разрешённые пакеты совпадают с requirements-dev.txt ----


def test_bad_extra_package_in_constitution_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path, with_section("Разрешённые пакеты", "- pip: ruff, pyright, requests"))
    assert packages_problems(root)


def test_bad_missing_package_in_constitution_is_rejected(tmp_path: Path) -> None:
    root = make_root(tmp_path, with_section("Разрешённые пакеты", "- pip: ruff"))
    assert packages_problems(root)


def test_packages_equal_to_requirements_without_versions_are_accepted(tmp_path: Path) -> None:
    root = make_root(tmp_path, with_section("Разрешённые пакеты", "- pip: ruff, pyright"))
    assert packages_problems(root) == []


def test_product_allowed_packages_match_requirements_dev() -> None:
    assert packages_problems(REPO) == []
