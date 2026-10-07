"""Общие помощники для тестов hooks: запуск hook как процесса и заготовка проекта."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "plugin" / "hooks"

CONSTITUTION = """# CONSTITUTION

## Целевая ОС

Windows 11

## Разрешённые пакеты
- pip: requests, pydantic
- npm: react, @types/node
- nuget: Newtonsoft.Json
- psgallery: Pester

## Другое
- pip: flask
"""


class HookResult:
    def __init__(self, process: "subprocess.CompletedProcess[bytes]") -> None:
        self.code = process.returncode
        self.stdout = process.stdout.decode("utf-8", errors="replace")
        self.stderr = process.stderr.decode("utf-8", errors="replace")

    @property
    def blocked(self) -> bool:
        return self.code == 2


def run_hook(
    script: str,
    payload: dict[str, Any] | str,
    project: Path,
    role: str | None = None,
    path_prefix: Path | None = None,
    **extra: Any,
) -> HookResult:
    """Запускает hook так же, как Claude Code: JSON на stdin, переменная CLAUDE_PROJECT_DIR."""
    body = payload if isinstance(payload, str) else ""
    if isinstance(payload, dict):
        base: dict[str, Any] = {
            "session_id": "s1",
            "cwd": str(project),
            "permission_mode": "default",
        }
        data: dict[str, Any] = {**base, **payload, **extra}
        if role:
            data["agent_type"] = role
        if data["permission_mode"] is None:
            del data["permission_mode"]
        body = json.dumps(data)
    interpreter_dir = str(Path(sys.executable).parent)  # `python` в hooks = интерпретатор тестов
    prefix = (str(path_prefix) + os.pathsep) if path_prefix else ""
    env = {
        **os.environ,
        "PATH": prefix + interpreter_dir + os.pathsep + os.environ.get("PATH", ""),
        "CLAUDE_PROJECT_DIR": str(project),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=body.encode("utf-8"),
        capture_output=True,
        env=env,
        check=False,
        timeout=180,
    )
    return HookResult(process)


def bash(command: str, event: str = "PreToolUse") -> dict[str, Any]:
    return {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command}}


def powershell(command: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "PowerShell",
        "tool_input": {"command": command},
    }


def file_call(tool: str, path: Path | str, event: str = "PreToolUse") -> dict[str, Any]:
    return {"hook_event_name": event, "tool_name": tool, "tool_input": {"file_path": str(path)}}


@pytest.fixture(autouse=True)
def no_ci_bash_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Урок «локально проходит, в CI падает» (LESSONS.md): CircleCI задаёт BASH_ENV, и bash ставит
    в PATH настоящие dotnet, node, pwsh впереди подставных команд; тест его не видит."""
    monkeypatch.delenv("BASH_ENV", raising=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Проект, подключённый к ProjectArchitect: есть CONSTITUTION.md, принятый и новый ADR."""
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "CONSTITUTION.md").write_text(CONSTITUTION, encoding="utf-8")
    (tmp_path / "docs" / "adr" / "0001-accepted.md").write_text(
        "# ADR-0001\n\n- Статус: accepted\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "adr" / "0002-proposed.md").write_text(
        "# ADR-0002\n\n- Статус: proposed\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def bare_project(tmp_path: Path) -> Path:
    """Проект без CONSTITUTION.md: ProjectArchitect к нему не подключён."""
    return tmp_path


JSCPD_VERSION = "5.4.0"
TS_SHOP = Path(__file__).resolve().parent / "projects" / "ts_shop"
CS_SHOP = Path(__file__).resolve().parent / "projects" / "cs_shop"
# Тест медленный, если ему нужна одна из этих фикстур: они ставят или запускают node, dotnet, pwsh
# и PSScriptAnalyzer. Остальные медленные тесты помечены `@pytest.mark.slow` вручную.
SLOW_FIXTURES = frozenset({"jscpd", "ts_node_modules", "prepared", "psa", "ps_shell"})
# Файлы тестов по языкам: CI в урезанном прогоне запускает медленные тесты только тех языков, чьи
# файлы изменил PR (`-m "not slow or lang_csharp"`), полный прогон перед слиянием запускает все.
LANGUAGE_FILES = {
    "test_ci_python": "python",
    "test_ci_typescript": "typescript",
    "test_ci_csharp": "csharp",
    "test_ci_powershell": "powershell",
}
SLOW_GROUPS = {
    "test_ci_csharp": "csharp",
    "test_ci_typescript": "typescript",
    "test_ci_powershell": "powershell",
}
# Ответы владельца, которые обязательны для /parch:init-project: целевая ОС и цель продукта.
INIT_ANSWERS: dict[str, Any] = {
    "target_os": "windows",
    "goal": {
        "summary": "Считает заказы магазина.",
        "audience": "Продавцы небольшого магазина.",
        "criteria": ["Сумма заказа считается верно", "Заказ можно посмотреть по номеру"],
        "out_of_scope": ["Оплата"],
    },
}


def npm_command() -> list[str]:
    """npm без обёрток .cmd: через node и npm-cli.js (одинаково на всех системах)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("нужен Node.js")
    npm_cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return [node, str(npm_cli)] if npm_cli.is_file() else [shutil.which("npm") or "npm"]


@pytest.fixture(scope="session")
def jscpd(tmp_path_factory: pytest.TempPathFactory) -> str:
    """jscpd той же версии, что в CI-шаблоне, установленный во временную папку."""
    folder = tmp_path_factory.mktemp("jscpd")
    (folder / "package.json").write_text('{"private": true}', encoding="utf-8")
    done = subprocess.run(
        [*npm_command(), "install", f"jscpd@{JSCPD_VERSION}", "--no-audit", "--no-fund"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    entry = folder / "node_modules" / "jscpd" / "run-jscpd.js"
    node = shutil.which("node") or "node"
    return f'"{node}" "{entry}"'.replace("\\", "/")


@pytest.fixture(scope="session")
def ts_node_modules(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """node_modules тестового TypeScript-проекта, установленные один раз по его lock-файлу."""
    folder = tmp_path_factory.mktemp("tsdeps")
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(TS_SHOP / name, folder / name)
    done = subprocess.run(
        [*npm_command(), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return folder / "node_modules"


def link_directory(link: Path, target: Path) -> None:
    """Ссылка на папку: junction на Windows (не требует прав), symlink на остальных системах."""
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    """Удаляет только ссылку, не содержимое того, на что она указывает."""
    if os.path.lexists(link):
        os.rmdir(link) if os.name == "nt" else link.unlink()


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "reports"


@pytest.hookimpl(tryfirst=True)  # раньше хука xdist: он добавляет имя группы к идентификатору теста
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Автоматически помечает медленные тесты и держит их по языкам на одном процессе xdist."""
    for item in items:
        if SLOW_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.slow)
        language = LANGUAGE_FILES.get(item.path.stem)
        if language:
            item.add_marker(getattr(pytest.mark, f"lang_{language}"))
        group = SLOW_GROUPS.get(item.path.stem)
        if group and item.get_closest_marker("slow"):
            item.add_marker(pytest.mark.xdist_group(group))
