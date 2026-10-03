"""Версии инструментов закреплены в CONSTITUTION.md и совпадают с CI-шаблонами и настройками."""

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import CS_SHOP, JSCPD_VERSION, TS_SHOP

REPO = Path(__file__).resolve().parent.parent
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
TEMPLATES = REPO / "plugin" / "templates"
ALL = ["python", "typescript", "csharp", "powershell"]


def load_init() -> Any:
    spec = importlib.util.spec_from_file_location("init_project_versions", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def section(languages: list[str]) -> str:
    text: str = load_init().render_constitution("Демо", "d", "p", languages, "Windows 11")
    match = re.search(r"## Версии инструментов\n(.*?)\n## ", text, re.S)
    assert match is not None, "в CONSTITUTION.md нет раздела «Версии инструментов»"
    return match.group(1)


def test_the_template_has_the_versions_section_before_the_commands() -> None:
    template = (TEMPLATES / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert "## Версии инструментов" in template
    assert "{{TOOL_VERSIONS}}" in template
    assert template.index("## Разрешённые пакеты") < template.index("## Версии инструментов")
    assert template.index("## Версии инструментов") < template.index("## Команды проверки")


@pytest.mark.parametrize("language", ALL)
def test_each_language_gets_only_its_own_versions(language: str) -> None:
    text = section([language])
    markers = {
        "python": "ruff ",
        "typescript": "knip ",
        "csharp": ".NET SDK",
        "powershell": "PSScriptAnalyzer",
    }
    assert markers[language] in text
    for other, marker in markers.items():
        if other != language:
            assert marker not in text, (language, other)


def test_python_versions_match_this_repository_pins() -> None:
    text = section(["python"])
    for line in (REPO / "requirements-dev.txt").read_text(encoding="utf-8").split():
        name, _, version = line.partition("==")
        if name in {"ruff", "pyright", "pytest", "pytest-cov", "coverage"}:
            assert f"{name} {version}" in text, name


def test_typescript_versions_match_the_sample_project_and_the_workflow() -> None:
    text = section(["typescript"])
    manifest = json.loads((TS_SHOP / "package.json").read_text(encoding="utf-8"))
    for name, version in manifest["devDependencies"].items():
        assert f"{name} {version}" in text, name
    workflow = (TEMPLATES / "ci" / "typescript.yml").read_text(encoding="utf-8")
    node = re.search(r'node-version: "([\d.]+)"', workflow)
    assert node is not None and f"Node {node.group(1)};" in text


def test_csharp_versions_match_the_sample_project() -> None:
    text = section(["csharp"])
    sdk = json.loads((CS_SHOP / "global.json").read_text(encoding="utf-8"))["sdk"]["version"]
    assert f".NET SDK {sdk};" in text
    files = [CS_SHOP / "Directory.Build.props", *CS_SHOP.rglob("*.csproj")]
    pins: dict[str, str] = {}
    for path in files:
        pins.update(re.findall(r'Include="([^"]+)"\s+Version="([^"]+)"', path.read_text("utf-8")))
    assert pins, "в тестовом проекте не найдены пакеты"
    for name, version in pins.items():
        assert f"{name} {version}" in text, name


def test_powershell_and_jscpd_versions_match_the_workflows_and_the_script() -> None:
    text = section(["powershell"])
    workflow = (TEMPLATES / "ci" / "powershell.yml").read_text(encoding="utf-8")
    pinned = re.search(r"-RequiredVersion ([\d.]+)", workflow)
    assert pinned is not None
    assert f"PowerShell 7 (pwsh); PSScriptAnalyzer {pinned.group(1)}" in text
    assert f"jscpd {JSCPD_VERSION} " in text


def test_every_version_in_the_section_is_exact() -> None:
    bullets = [line for line in section(ALL).splitlines() if line.startswith("- ")]
    assert len(bullets) == 5  # jscpd и четыре языка
    for line in bullets:
        assert not re.search(r"[\^~*]|latest", line), line
        assert re.search(r"\d+\.\d+", line), line


# ---------- версия стандарта ----------


def test_the_constitution_template_names_the_standard_version() -> None:
    from parch_ci import STANDARD_VERSION

    text: str = load_init().render_constitution("Демо", "d", "p", ["python"], "Windows 11")
    assert f"ProjectArchitect {STANDARD_VERSION}" in text
    assert "{{" not in text  # все места подстановки заполнены


def test_the_standard_document_has_the_version_the_checks_implement() -> None:
    from parch_ci import STANDARD_VERSION

    standard = (REPO / "docs" / "STANDARD.md").read_text(encoding="utf-8")
    assert f"версия {STANDARD_VERSION} ·" in standard.splitlines()[2]
