# ruff: noqa: E501, E402
"""Помощник для `/parch:trace`: по файлу или функции показывает, зачем нужен код (F18).

Вход: JSON-объект на stdin: {"project_dir": ".", "target": "src/shop/orders.py"} (путь к файлу или папке модуля, либо имя
публичной функции из каталога возможностей). Выход: JSON-объект на stdout с цепочкой «цель → блок → спецификация → тесты
приёмки → модуль». Скрипт только читает `docs/MODULES.md`, `state/features.json`, `docs/GOAL.md`, `docs/CAPABILITIES.md`.
Если код не относится ни к одному модулю или у модуля нет блока, об этом сказано прямо (`found`: false).
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, cast

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "templates" / "ci" / "parch"))

import parch_ci  # noqa: E402
import parch_status  # noqa: E402

CATALOG_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*$")


def norm(path: str) -> str:
    return (
        PurePosixPath(path.strip().strip("`").replace("\\", "/"))
        .as_posix()
        .removeprefix("./")
        .rstrip("/")
    )


def catalog_rows(project: Path) -> list[dict[str, str]]:
    path = project / "docs" / "CAPABILITIES.md"
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        found = CATALOG_ROW.match(line)
        if found and found.group(1) != "Модуль":
            rows.append(
                {
                    "file": norm(found.group(1)),
                    "name": found.group(2),
                    "description": found.group(3),
                }
            )
    return rows


def blocks_of(project: Path) -> dict[str, dict[str, Any]]:
    path = project / "state" / "features.json"
    if not path.is_file():
        return {}
    items = parch_ci.as_list(parch_ci.read_json(path).get("features"))
    return {
        str(parch_ci.as_dict(i).get("id")): parch_ci.as_dict(i)
        for i in items
        if parch_ci.as_dict(i).get("id")
    }


def find_module(target: str, rows: list[parch_ci.ModuleRow]) -> parch_ci.ModuleRow | None:
    """Модуль, чей путь равен цели или является её началом (самый длинный путь побеждает)."""
    best: parch_ci.ModuleRow | None = None
    for row in rows:
        if target == row.path or target.startswith(row.path + "/"):
            if best is None or len(row.path) > len(best.path):
                best = row
    return best


def trace(project: Path, target: str) -> dict[str, Any]:
    goal_name, _, criteria = parch_status.read_goal(project)
    _, rows = parch_ci.modules_with_blocks(project)
    catalog = catalog_rows(project)
    wanted = norm(target)
    function: dict[str, str] | None = None
    path = wanted
    named = [r for r in catalog if r["name"] == target or r["name"].endswith("." + target)]
    if named and not (project / wanted).exists():
        function = named[0]
        path = function["file"]
    module = find_module(path, rows)
    if module is None:
        return {
            "found": False,
            "target": target,
            "message": f"«{target}» не относится ни к одному модулю из docs/MODULES.md, поэтому и ни к одному блоку и цели",
        }
    known = blocks_of(project)
    blocks: list[dict[str, Any]] = []
    for block_id in module.blocks:
        raw = known.get(block_id)
        goals = [str(g) for g in parch_ci.as_list(raw.get("goal"))] if raw else []
        blocks.append(
            {
                "id": block_id,
                "known": raw is not None,
                "title": str(raw.get("title", "")) if raw else "",
                "status": str(raw.get("status", "")) if raw else "",
                "spec": str(raw.get("spec", "")) if raw else "",
                "acceptance_tests": [str(t) for t in parch_ci.as_list(raw.get("acceptance_tests"))]
                if raw
                else [],
                "goals": [{"id": g, "text": criteria.get(g, "")} for g in goals],
            }
        )
    result: dict[str, Any] = {
        "found": True,
        "target": target,
        "project": goal_name,
        "module": {"path": module.path, "status": module.status},
        "function": function,
        "blocks": blocks,
    }
    if not blocks:
        result["message"] = (
            f"модуль {module.path} найден, но блока у него нет: код без цели, кандидат на удаление (решение за владельцем)"
        )
    return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        request = cast("dict[str, Any]", json.loads(sys.stdin.read() or "{}"))
        project = Path(str(request.get("project_dir", "."))).resolve()
        target = str(request.get("target", "")).strip()
        if not target:
            raise ValueError("нужен target: путь к файлу или имя функции")
        if not project.is_dir():
            raise ValueError(f"нет папки проекта: {project}")
        result = trace(project, target)
    except (ValueError, OSError, parch_ci.ToolError) as error:
        sys.stderr.write(f"trace: {error}\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
