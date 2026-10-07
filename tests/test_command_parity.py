"""Команды проверок в CONSTITUTION (список init) и в шаблонах CircleCI запускают одно и то же.

Семейство ошибок «локально проходит, в CI падает» (Windows и Linux, BASH_ENV, `python -m pytest`
и `pytest`): `python -m pytest` добавляет корень проекта в путь импорта и скрывает
`ModuleNotFoundError`, который CI покажет. Поэтому форма запуска (инструмент и подкоманда) у команды
из списка init должна совпадать с формой запуска в `plugin/templates/ci/circleci/<язык>.yml`.
Плохой пример: в CONSTITUTION `python -m pytest`, в CI `pytest` — красный.
"""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
CIRCLECI = ROOT / "plugin" / "templates" / "ci" / "circleci"
LANGUAGES = ["python", "typescript", "csharp", "powershell"]


def init_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_project_parity", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass ищет свой модуль по имени
    spec.loader.exec_module(module)
    return module


def invocation(command: str) -> tuple[str, ...]:
    """Форма запуска: инструмент и подкоманды до первого флага (`python -m X` целиком)."""
    tokens = command.split()
    if tokens[:1] == ["timeout"]:
        tokens = tokens[2:]  # `timeout 600 pytest ...`
    head: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-m" and index + 1 < len(tokens):
            head += [token, tokens[index + 1]]
            index += 2
            continue
        if token.startswith("-"):
            break
        head.append(token)
        index += 1
    return tuple(head)


def ci_commands(text: str) -> list[str]:
    """Строки `command:` (в том числе блоки `|`) из шаблона задания CircleCI."""
    commands: list[str] = []
    lines = text.splitlines()
    for number, line in enumerate(lines):
        key = re.match(r"^(\s*)command:[ \t]*(.*)$", line)
        if key is None:
            continue
        indent, rest = len(key.group(1)), key.group(2).strip()
        if rest and rest != "|":
            commands.append(rest)
            continue
        for follow in lines[number + 1 :]:  # блок `|`: строки глубже ключа
            if follow.strip() and len(follow) - len(follow.lstrip()) <= indent:
                break
            if follow.strip():
                commands.append(follow.strip())
    return commands


def mismatches(constitution_commands: list[str], ci: list[str]) -> list[str]:
    """Команды из CONSTITUTION, форма запуска которых не встречается среди команд CI."""
    ci_forms = {invocation(command) for command in ci}
    return [c for c in constitution_commands if invocation(c) not in ci_forms]


@pytest.mark.parametrize("language", LANGUAGES)
def test_check_commands_in_the_constitution_run_the_same_way_as_in_ci(language: str) -> None:
    commands = list(init_module().LANGUAGES[language].checks)
    ci = ci_commands((CIRCLECI / f"{language}.yml").read_text(encoding="utf-8"))
    assert commands and ci
    assert mismatches(commands, ci) == [], (
        f"команды из CONSTITUTION запускаются иначе, чем в CI ({language}): "
        f"{mismatches(commands, ci)}"
    )


def test_pytest_through_python_m_is_a_mismatch_with_plain_pytest_in_ci() -> None:
    """Плохой пример: так CI был красным, пока локально всё проходило (`No module named 'src'`)."""
    ci = ["timeout 600 pytest -v --junitxml=test-report.xml", "ruff check ."]
    assert mismatches(["python -m pytest -q"], ci) == ["python -m pytest -q"]
    assert mismatches(["pytest -q"], ci) == []
    assert mismatches(["python -m ruff check ."], ci) == ["python -m ruff check ."]
    assert mismatches(["ruff check ."], ci) == []


def test_the_stop_gate_fallback_commands_run_the_same_way_as_in_ci(tmp_path: Path) -> None:
    """Запасные команды шлюза Stop (без «Команды проверки») совпадают с CI, как список init."""
    sys.path.insert(0, str(ROOT / "plugin" / "hooks"))
    import stop_gate

    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.pyright]\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    commands = stop_gate.detected_commands(tmp_path)
    assert len(commands) == 3  # pytest, ruff, pyright
    ci = ci_commands((CIRCLECI / "python.yml").read_text(encoding="utf-8"))
    assert mismatches(commands, ci) == []


def test_a_tool_that_the_real_python_template_does_not_run_is_a_mismatch() -> None:
    """Плохой пример на настоящем шаблоне: CI перестал запускать `ruff check`, проверка красная."""
    ci = ci_commands((CIRCLECI / "python.yml").read_text(encoding="utf-8"))
    without_ruff = [command for command in ci if not command.startswith("ruff check")]
    assert mismatches(["ruff check ."], ci) == []
    assert mismatches(["ruff check ."], without_ruff) == ["ruff check ."]


def test_a_different_subcommand_is_a_mismatch() -> None:
    ci = ["dotnet build --no-restore -warnaserror", "npm test"]
    assert mismatches(["dotnet test"], ci) == ["dotnet test"]
    assert mismatches(["dotnet build -warnaserror", "npm test"], ci) == []


def test_every_ci_template_yields_commands_to_compare() -> None:
    """Разбор шаблона не молчит: в каждом есть команды `.github/parch/parch_ci.py ...`."""
    for language in LANGUAGES:
        ci = ci_commands((CIRCLECI / f"{language}.yml").read_text(encoding="utf-8"))
        assert any(".github/parch/parch_ci.py" in command for command in ci), language
