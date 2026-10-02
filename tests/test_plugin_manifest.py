"""Манифест плагина и каталога должны быть согласованы: иначе плагин не установится."""

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_manifest_has_name() -> None:
    manifest = _load(REPO / "plugin" / ".claude-plugin" / "plugin.json")
    assert manifest["name"] == "parch"


def test_marketplace_points_to_existing_plugin() -> None:
    market = _load(REPO / ".claude-plugin" / "marketplace.json")
    plugins: list[dict[str, Any]] = market["plugins"]
    assert len(plugins) == 1
    entry = plugins[0]
    assert entry["name"] == "parch"
    assert (REPO / str(entry["source"]) / ".claude-plugin" / "plugin.json").is_file()


def test_components_live_at_plugin_root_not_in_manifest_dir() -> None:
    inner = REPO / "plugin" / ".claude-plugin"
    assert [p.name for p in inner.iterdir()] == ["plugin.json"]
    assert (REPO / "plugin" / "hooks" / "hooks.json").is_file()
