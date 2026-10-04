# ruff: noqa: E501
"""Одна команда «проверить перед отправкой» (docs/LESSONS.md: автоматика вместо перечня команд).

    python scripts/preflight.py                    ruff, формат, pyright, standard, полный прогон тестов
    python scripts/preflight.py --update-baseline  то же, затем обновление baseline по чистому отчёту

Останавливается на первой ошибке. Если PR меняет только текст (`.github/scope.py`), идёт один `standard`:
тесты смысл текста не проверяют (AGENTS.md, «Перед каждой отправкой»).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARCH_CI = "plugin/templates/ci/parch/parch_ci.py"
Step = tuple[str, list[str]]


def plan(code_changed: bool, python: str = sys.executable) -> list[Step]:
    """Шаги проверки: дешёвые первыми, полный прогон последним."""
    standard: Step = ("standard", [python, PARCH_CI, "standard"])
    if not code_changed:
        return [standard]
    return [
        ("ruff check", [python, "-m", "ruff", "check", "."]),
        ("ruff format --check", [python, "-m", "ruff", "format", "--check", "."]),
        ("pyright", [python, "-m", "pyright"]),
        standard,
        (
            "полный прогон тестов",
            [
                python,
                "-m",
                "pytest",
                "-m",
                "",
                "--junitxml=test-report.xml",
                "-p",
                "no:cacheprovider",
            ],
        ),
    ]


def baseline_step(python: str = sys.executable, platform: str | None = None) -> Step:
    system = platform or ("windows" if os.name == "nt" else "posix")
    return (
        "baseline",
        [python, PARCH_CI, "baseline", "--update", "--only-tests", "--report-platform", system,
         "--report", "test-report.xml"],
    )  # fmt: skip


def run(steps: list[Step], execute: Callable[[list[str]], int], say: Callable[[str], None]) -> int:
    """Выполняет шаги по порядку; код первого упавшего шага становится кодом выхода."""
    for index, (title, argv) in enumerate(steps, start=1):
        say(f"[{index}/{len(steps)}] {title}")
        started = time.monotonic()
        code = execute(argv)
        seconds = round(time.monotonic() - started)
        if code != 0:
            say(
                f"ОСТАНОВКА: «{title}» не прошёл (код {code}, {seconds} с). Исправьте и запустите снова."
            )
            return code
        say(f"    ок, {seconds} с")
    say("Всё прошло: можно отправлять.")
    return 0


def changed_files(root: Path) -> list[str] | None:
    """Файлы, изменённые относительно origin/main, включая неотслеживаемые (None: git не ответил)."""
    outputs: list[str] = []
    for argv in (
        ["git", "diff", "--name-only", "origin/main"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        done = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=120,
        )
        if done.returncode != 0:
            return None
        outputs += done.stdout.splitlines()
    return [line.strip() for line in outputs if line.strip()]


def code_changed(root: Path) -> bool:
    """True, если PR меняет что-то кроме текста; при любой неясности считается, что меняет."""
    files = changed_files(root)
    if files is None or not files:
        return True
    spec = importlib.util.spec_from_file_location("pr_scope", root / ".github" / "scope.py")
    if spec is None or spec.loader is None:
        return True
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.scope(files)["code"] == "true"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    changed = code_changed(ROOT)
    steps = plan(changed)
    if "--update-baseline" in args and changed:
        steps.append(baseline_step())
    if not changed:
        print("PR меняет только текст: идёт один standard, тесты не нужны.")

    def execute(command: list[str]) -> int:
        return subprocess.run(command, cwd=ROOT, check=False).returncode

    return run(steps, execute, print)


if __name__ == "__main__":
    sys.exit(main())
