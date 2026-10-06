# ruff: noqa: E501
"""Версия плагина и тег выпуска: «plugin/ менялся после тега, а версия нет» должно быть видно (docs/LESSONS.md)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "release_check.py"
MANIFEST = Path("plugin") / ".claude-plugin" / "plugin.json"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_check_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release: Any = load()


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def write(root: Path, rel: Path | str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def set_version(root: Path, version: str) -> None:
    write(root, MANIFEST, json.dumps({"name": "parch", "version": version}))


def commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def project(tmp_path: Path, version: str = "0.1.0", tag: str | None = "v0.1.0") -> Path:
    git(tmp_path, "init", "-q")
    set_version(tmp_path, version)
    write(tmp_path, "plugin/skills/a/SKILL.md", "один")
    commit(tmp_path, "выпуск")
    if tag:
        git(tmp_path, "tag", tag)
    return tmp_path


def run(root: Path, strict: bool) -> tuple[list[str], int]:
    return release.render(release.evaluate(root), strict)


# ---------- заведомо плохой пример: изменили plugin/, версию не подняли ----------


def test_plugin_changed_after_the_tag_without_a_version_bump_is_reported(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "plugin/skills/a/SKILL.md", "два")
    commit(root, "правка плагина")
    lines, code = run(root, strict=False)
    assert code == 0  # между выпусками менять plugin/ можно: показываем, не блокируем
    assert any("изменено файлов в plugin/: 1" in line for line in lines)


def test_the_same_drift_fails_in_strict_mode_which_the_release_pr_uses(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "plugin/skills/a/SKILL.md", "два")
    commit(root, "правка плагина")
    lines, code = run(root, strict=True)
    assert code == 1
    assert any(line.startswith("ОШИБКА") and "поднимите version" in line for line in lines)


def test_uncommitted_plugin_changes_count_as_drift_too(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "plugin/skills/a/SKILL.md", "два")
    assert release.evaluate(root).drift


# ---------- хорошие случаи ----------


def test_a_version_bump_with_changes_passes_strict_mode(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "plugin/skills/a/SKILL.md", "два")
    set_version(root, "0.1.1")
    commit(root, "выпуск 0.1.1")
    lines, code = run(root, strict=True)
    assert code == 0
    assert any("поставьте тег v0.1.1" in line for line in lines)


def test_nothing_changed_since_the_tag_is_quiet_and_green(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, "docs/note.md", "вне плагина")
    commit(root, "текст")
    lines, code = run(root, strict=True)
    assert code == 0
    assert not release.evaluate(root).changed
    assert not any("ОШИБКА" in line for line in lines)


def test_a_bare_manifest_change_is_not_counted_as_plugin_drift(tmp_path: Path) -> None:
    root = project(tmp_path)
    set_version(root, "0.1.1")
    commit(root, "только версия")
    assert release.evaluate(root).changed == []


def test_no_release_tags_is_a_message_in_the_plain_mode(tmp_path: Path) -> None:
    root = project(tmp_path, tag=None)
    write(root, "plugin/skills/a/SKILL.md", "два")
    commit(root, "правка")
    lines, code = run(root, strict=False)
    assert code == 0
    assert any("тегов выпуска" in line.lower() for line in lines)


def test_no_release_tags_is_an_error_in_strict_mode(tmp_path: Path) -> None:
    root = project(tmp_path, tag=None)
    lines, code = run(root, strict=True)
    assert code == 1
    assert any(line.startswith("ОШИБКА") and "тегов выпуска" in line for line in lines)


def test_a_bump_without_plugin_changes_says_so_instead_of_files_unchanged(tmp_path: Path) -> None:
    root = project(tmp_path)
    set_version(root, "0.1.1")
    commit(root, "только версия")
    lines, code = run(root, strict=True)
    assert code == 0
    assert any("поднята без изменений plugin/" in line for line in lines)
    assert not any("не менялись" in line for line in lines)


# ---------- нарушения, которых не бывает никогда (даже без --strict) ----------


def test_a_version_lower_than_the_tag_is_always_an_error(tmp_path: Path) -> None:
    root = project(tmp_path, version="0.2.0", tag="v0.2.0")
    set_version(root, "0.1.9")
    commit(root, "откат версии")
    lines, code = run(root, strict=False)
    assert code == 1
    assert any("ниже версии тега" in line for line in lines)


def test_a_tag_that_does_not_match_the_version_inside_it_is_an_error(tmp_path: Path) -> None:
    root = project(tmp_path, version="0.1.0", tag="v0.3.0")
    lines, code = run(root, strict=False)
    assert code == 1
    assert any("не равен v0.1.0" in line for line in lines)


@pytest.mark.parametrize("bad", ["0.2.0-beta", "1.x", ""])
def test_a_version_that_cannot_be_parsed_is_an_error_not_a_silent_skip(
    tmp_path: Path, bad: str
) -> None:
    root = project(tmp_path)
    set_version(root, bad)
    commit(root, "версия с суффиксом")
    lines, code = run(root, strict=False)
    assert code == 1
    assert any("нельзя разобрать" in line for line in lines)


def test_a_manifest_without_a_version_is_an_error(tmp_path: Path) -> None:
    root = project(tmp_path)
    write(root, MANIFEST, json.dumps({"name": "parch"}))
    commit(root, "сломали манифест")
    assert run(root, strict=False)[1] == 1


# ---------- запуск как команды, и сам репозиторий ----------


def test_the_command_runs_on_the_real_repository_without_crashing() -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict"], cwd=REPO, capture_output=True, check=False
    )
    # запуск идёт из корня настоящего репозитория: код выхода только 0 или 1, без падения скрипта
    assert done.returncode in (0, 1)
    assert b"Traceback" not in done.stderr


def test_the_real_manifest_has_a_parseable_version() -> None:
    manifest = json.loads((REPO / MANIFEST).read_text(encoding="utf-8"))
    assert release.parse(manifest["version"]) is not None
