# ruff: noqa: E501
"""Горячие точки по истории git (docs/design/analyze-existing.md, раздел «Анализ истории git»; STANDARD.md, раздел 8).

Горячая точка: большой файл с кодом, который часто меняется; особенно опасна, если у неё нет теста.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

CODE_SUFFIXES = {".py", ".cs", ".ts", ".tsx", ".ps1", ".psm1"}
MAX_COMMITS = 5000
TOP = 10


def git_names(project: Path) -> list[str] | None:
    """Имена файлов из последних коммитов (по одной строке на каждое изменение файла)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(project), "-c", "core.quotepath=off", "log", f"-n{MAX_COMMITS}", "--name-only", "--pretty=format:"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def has_test(path: str, all_paths: set[str]) -> bool:
    """Есть ли в проекте файл теста с похожим именем (test_<имя>, <Имя>Tests, <имя>.test, <имя>.Tests)."""
    stem = Path(path).stem
    lowered = stem.lower()
    for other in all_paths:
        name = Path(other).stem.lower()
        if other == path:
            continue
        if name in {
            f"test_{lowered}",
            f"{lowered}_test",
            f"{lowered}tests",
            f"{lowered}.test",
            f"{lowered}.tests",
            f"{lowered}.spec",
        }:
            return True
    return False


def hotspots(project: Path, rel: list[str], top: int = TOP) -> list[dict[str, Any]]:
    """Топ файлов по (число изменений × число строк); только код, существующий сейчас."""
    names = git_names(project)
    if not names:
        return []
    existing = set(rel)
    counts: dict[str, int] = {}
    for name in names:
        if name in existing and Path(name).suffix.lower() in CODE_SUFFIXES:
            counts[name] = counts.get(name, 0) + 1
    rows: list[dict[str, Any]] = []
    for name, changes in counts.items():
        try:
            lines = len((project / name).read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        if re.search(r"(^|/)(tests?/|test_)", name):
            continue  # сам тест не горячая точка проекта
        rows.append(
            {
                "path": name,
                "changes": changes,
                "lines": lines,
                "score": changes * lines,
                "has_test": has_test(name, existing),
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["path"]))
    return rows[:top]
