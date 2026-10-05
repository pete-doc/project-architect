"""Подключает проект к ProjectArchitect для `/parch:init-project`.

Вход: JSON-объект на stdin. Выход: JSON-отчёт на stdout.

    {"project_dir": ".", "name": "Мой сервис", "languages": ["python"],
     "description": "...", "priorities": "..."}

Ничего не перезаписывает: существующие файлы пропускаются и попадают в отчёт. CONSTITUTION.md
создаётся последним, потому что его появление включает защитные hooks в проекте.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = PLUGIN_ROOT / "templates"
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "adr" / "scripts"))
sys.path.insert(0, str(PLUGIN_ROOT / "templates" / "ci" / "parch"))

import adr  # noqa: E402  (путь добавлен строкой выше)
import parch_ci  # noqa: E402


@dataclass(frozen=True)
class Language:
    title: str
    ecosystem: str
    packages: str
    checks: tuple[str, ...]


CI_SCRIPT = ".github/parch/parch_ci.py"
LOCAL_CI_CHECKS = ("tests", "modules", "deps", "dead-code", "architecture")
# Версии инструментов TypeScript зафиксированы точно (без ^ и ~): CI не должен ломаться
# от выхода новой версии. TypeScript 7 (нативный компилятор) не подходит: у него нет JS API,
# на котором работают dependency-cruiser и knip (dependency-cruiser молча анализирует 0 файлов).
TS_TOOL_VERSIONS = {
    "@biomejs/biome": "2.5.15",
    "@types/node": "22.19.1",
    "@vitest/coverage-v8": "5.0.3",
    "dependency-cruiser": "18.5.0",
    "knip": "6.39.0",
    "typescript": "5.9.3",
    "vitest": "5.0.3",
}
TS_REPORT_TEST = (
    "vitest run --coverage --reporter=default --reporter=json --outputFile.json=test-report.json"
)
TS_SCRIPTS = {
    "typecheck": "tsc --noEmit",
    "lint": "biome ci .",
    "test": TS_REPORT_TEST,
    "arch": "depcruise src --config .dependency-cruiser.json",
    "deadcode": "knip",
}
TS_CHECK_FLAGS = "--language typescript --report test-report.json"
TS_LOCAL_CI_CHECKS = ("settings", "tests", "skips", "modules", "deps", "dead-code", "architecture")

# C#: версии пакетов лежат в шаблонах (plugin/templates/csharp) и в lock-файлах; .NET SDK
# закреплён в global.json точной версией (rollForward: disable).
CS_PACKAGES = (
    "Microsoft.NET.Test.Sdk, xunit, xunit.runner.visualstudio, coverlet.collector, "
    "TngTech.ArchUnitNET.xUnit, Roslynator.Analyzers"
)
CS_TEST_COMMAND = (
    'dotnet test --logger trx --results-directory test-results --collect "XPlat Code Coverage"'
)
CS_CHECK_FLAGS = "--language csharp --report test-results"
CS_LOCAL_CI_CHECKS = ("settings", "tests", "skips", "modules", "deps", "dead-code", "architecture")

# PowerShell: только тонкий «клей» (ADR-0010); тестов, покрытия, архитектуры и дублей нет.
PS_CHECK_FLAGS = "--language powershell"
PS_LOCAL_CI_CHECKS = ("psscriptanalyzer", "thin", "suppressions", "settings")
PS_THRESHOLDS = """
## Пороги тонкости PowerShell

Скрипты PowerShell только запускают программы; логика живёт в Python или C# (ADR-0010).
Команды, циклы, условия и функции считает разбор PowerShell (AST);
общий бюджет действует на весь PowerShell проекта.

- max_commands: 15
- max_loops: 0
- max_conditions: 2
- max_functions: 0
- max_total_commands: 30
- max_total_conditions: 4
"""

LANGUAGES = {
    "python": Language(
        "Python 3.12+ (ruff, pyright, pytest, import-linter, vulture, deptry, jscpd)",
        "pip",
        "pytest, pytest-cov, coverage, ruff, pyright, import-linter, vulture, deptry",
        (
            "python -m pytest -q",
            "python -m ruff check .",
            "python -m ruff format --check .",
            "python -m pyright",
            *(f"python {CI_SCRIPT} {check}" for check in LOCAL_CI_CHECKS),
        ),
    ),
    "typescript": Language(
        "TypeScript 5.9 (tsc strict, Biome, Vitest, dependency-cruiser, knip, jscpd)",
        "npm",
        ", ".join(TS_TOOL_VERSIONS),
        (
            "npm run typecheck",
            "npm run lint",
            "npm test",
            *(f"python {CI_SCRIPT} {check} {TS_CHECK_FLAGS}" for check in TS_LOCAL_CI_CHECKS),
        ),
    ),
    "csharp": Language(
        "C# (.NET 10, nullable, warnings as errors, анализаторы, ArchUnitNET, xUnit, jscpd)",
        "nuget",
        CS_PACKAGES,
        (
            "dotnet build -warnaserror",
            "dotnet format --verify-no-changes",
            CS_TEST_COMMAND,
            *(f"python {CI_SCRIPT} {check} {CS_CHECK_FLAGS}" for check in CS_LOCAL_CI_CHECKS),
        ),
    ),
    "powershell": Language(
        "PowerShell (только тонкий «клей» для запуска программ: PSScriptAnalyzer, пороги тонкости)",
        "psgallery",
        "PSScriptAnalyzer",
        tuple(f"python {CI_SCRIPT} {check} {PS_CHECK_FLAGS}" for check in PS_LOCAL_CI_CHECKS),
    ),
}
CI_TEMPLATES = {
    "python": "python.yml",
    "typescript": "typescript.yml",
    "csharp": "csharp.yml",
    "powershell": "powershell.yml",
}
GITIGNORE_LINES = [".claude/audit/", ".claude/settings.local.json"]
GITIGNORE_BY_LANGUAGE = {
    "typescript": ["node_modules/", "coverage/", "test-report.json"],
    "csharp": ["bin/", "obj/", "test-results/", "TestResults/"],
}

PYPROJECT = """[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.pyright]
pythonVersion = "3.12"
typeCheckingMode = "strict"
extraPaths = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
"""
# Версии инструментов проверки зафиксированы: CI не должен ломаться от выхода новой версии.
REQUIREMENTS_DEV = (
    "ruff==0.16.10\npyright==1.1.414\npytest==9.1.1\npytest-cov==7.1.0\n"
    "import-linter==2.15\nvulture==2.16\ndeptry==0.25.1\ncoverage==7.16.2\n"
)
REQUIREMENTS = "# Рабочие зависимости. Только пакеты из «Разрешённые пакеты» в CONSTITUTION.md.\n"
SMOKE_TEST = '''"""Начальный тест: проверки проекта запускаются. Замените его настоящими тестами."""


def test_smoke() -> None:
    assert True
'''


def initial_baseline(
    project: Path, languages: list[str], existing_code: bool = False
) -> dict[str, Any]:
    """Начальный baseline: известные тесты, пропуски, подавления и настройки каждого языка.

    `existing_project: true` ставится, если в проекте уже был код при подключении: новые правила
    прослеживаемости для такого проекта сначала только предупреждают (колонка «Блок» в
    MODULES.md), пока владелец не запишет долг.
    """
    first_tests = {
        "python": ["tests/test_smoke.py::test_smoke"],
        "typescript": ["smoke.test.ts::smoke"],
        "csharp": ["App.Tests.SmokeTests.Smoke"],
    }
    baseline: dict[str, Any] = {
        "version": 1,
        "tests": {},
        "dead_code": {},
        "skips": {},
        "suppressions": {},
        "skipped_tests": {},
        "config": {},
    }
    if existing_code:
        baseline["existing_project"] = True
    for lang in languages:
        rules = parch_ci.RULES[lang]
        if lang in first_tests:
            baseline["tests"][lang] = first_tests[lang]
            baseline["dead_code"][lang] = []
            baseline["skipped_tests"][lang] = []
        baseline["skips"][lang] = parch_ci.scan_counts(project, lang, rules.skip_patterns, True)
        baseline["suppressions"][lang] = parch_ci.scan_counts(
            project, lang, rules.suppression_patterns, False, in_comments=True
        )
        baseline["config"][lang] = parch_ci.settings_fingerprint(project, lang)
    return baseline


class Report:
    def __init__(self, project: Path) -> None:
        self.project = project
        self.created: list[str] = []
        self.skipped: list[str] = []
        self.notes: list[str] = []

    def write(self, rel: str, content: str | bytes) -> None:
        target = self.project / rel
        if target.exists():
            self.skipped.append(rel)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
        self.created.append(rel)

    def copy(self, template: str, rel: str) -> None:
        self.write(rel, (TEMPLATES / template).read_bytes().replace(b"\r\n", b"\n"))


def bullets(text: str, fallback: str) -> str:
    lines = [line.strip().lstrip("-*• ").strip() for line in text.splitlines() if line.strip()]
    return "\n".join(f"- {line}" for line in lines) if lines else fallback


TARGET_OS = {"windows": "Windows 11", "macos": "macOS", "linux": "Linux"}
BUDGET_NOTE = (
    "Бюджет на GitHub Actions: задайте небольшой, но не нулевой (например, 5 долларов) на странице "
    "https://github.com/settings/billing/budgets. Нулевой бюджет останавливает CI посреди работы "
    "без предупреждения, а без бюджета об ошибке настройки вы узнаете только из счёта."
)
NO_GOAL_YET = "(владелец пока не назвал)"


@dataclass(frozen=True)
class Goal:
    summary: str
    audience: str
    criteria: tuple[str, ...]
    out_of_scope: tuple[str, ...]


def normalize_target_os(value: object) -> str:
    """Целевая ОС: на ней работает исполнитель; CI всё равно на Linux (STANDARD.md, 7.2)."""
    text = str(value or "").strip().lower()
    for key, name in TARGET_OS.items():
        if text.startswith(key) or text.startswith(key[:3]):
            return name
    raise ValueError(
        f"target_os: выберите одну из систем ({', '.join(TARGET_OS.values())}); получено «{value}»"
    )


def text_list(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"goal.{field_name}: нужен список строк")
    items = [str(item).strip() for item in cast("list[object]", value)]
    return tuple(item for item in items if item)


def parse_goal(raw: object) -> Goal:
    """Цель продукта из разговора с владельцем: без неё планирование не начинается (6.5)."""
    if not isinstance(raw, dict):
        raise ValueError("нужна цель продукта (goal): что это, для кого, критерии готовности")
    data = cast("dict[str, object]", raw)
    summary = str(data.get("summary", "")).strip()
    audience = str(data.get("audience", "")).strip()
    criteria = text_list(data.get("criteria"), "criteria")
    if not summary:
        raise ValueError("goal.summary: опишите, что это за продукт")
    if not audience:
        raise ValueError("goal.audience: назовите, для кого продукт")
    if not criteria:
        raise ValueError(
            "goal.criteria: нужен хотя бы один проверяемый критерий готовности продукта"
        )
    return Goal(summary, audience, criteria, text_list(data.get("out_of_scope"), "out_of_scope"))


def render_goal(name: str, goal: Goal) -> str:
    template = (TEMPLATES / "docs" / "GOAL.md").read_text(encoding="utf-8")
    criteria = "\n".join(f"- **G{i}.** {text}" for i, text in enumerate(goal.criteria, start=1))
    out = "\n".join(f"- {text}" for text in goal.out_of_scope) or f"- {NO_GOAL_YET}"
    values = {
        "{{PROJECT_NAME}}": name,
        "{{SUMMARY}}": goal.summary,
        "{{AUDIENCE}}": goal.audience,
        "{{CRITERIA}}": criteria,
        "{{OUT_OF_SCOPE}}": out,
    }
    for key, value in values.items():
        template = template.replace(key, value)
    return template


TS_NODE_VERSION = "22.14.0"
CS_EXTRA_PACKAGES = {"TngTech.ArchUnitNET.xUnit": "0.13.4"}
PACKAGE_VERSION = re.compile(r'Include="([^"]+)"\s+Version="([^"]+)"')


def tool_versions_text(languages: list[str]) -> str:
    """Точные версии инструментов выбранных языков: те же, что в шаблонах CI и настройках."""
    lines = [f"- jscpd {parch_ci.JSCPD_VERSION} (дубли; запускается через Node, все языки)"]
    for lang in languages:
        if lang == "python":
            pins = (line.split("==") for line in REQUIREMENTS_DEV.split())
            lines.append("- Python 3.12; " + ", ".join(f"{n} {v}" for n, v in pins))
        elif lang == "typescript":
            tools = ", ".join(f"{n} {v}" for n, v in TS_TOOL_VERSIONS.items())
            lines.append(f"- Node {TS_NODE_VERSION}; {tools}")
        elif lang == "csharp":
            sdk = json.loads((TEMPLATES / "csharp" / "global.json").read_text(encoding="utf-8"))
            texts = [
                (TEMPLATES / "csharp" / name).read_text(encoding="utf-8")
                for name in ("Directory.Build.props", "App.Tests.csproj")
            ]
            found = {n: v for text in texts for n, v in PACKAGE_VERSION.findall(text)}
            found.update(CS_EXTRA_PACKAGES)
            tools = ", ".join(f"{n} {v}" for n, v in found.items())
            lines.append(f"- .NET SDK {sdk['sdk']['version']}; {tools}")
        elif lang == "powershell":
            lines.append(f"- PowerShell 7 (pwsh); PSScriptAnalyzer {parch_ci.PSA_VERSION}")
    return "\n".join(lines)


def render_constitution(
    name: str, description: str, priorities: str, languages: list[str], target_os: str
) -> str:
    template = (TEMPLATES / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    chosen = [LANGUAGES[lang] for lang in languages]
    stack = "\n".join(f"- {item.title}" for item in chosen)
    packages = "\n".join(f"- {item.ecosystem}: {item.packages}" for item in chosen)
    commands = [cmd for item in chosen for cmd in item.checks]
    check_text = "\n".join(f"- {cmd}" for cmd in commands)
    values = {
        "{{PROJECT_NAME}}": name,
        "{{DESCRIPTION}}": description.strip() or "(Опишите проект одной-двумя фразами.)",
        "{{PRIORITIES}}": bullets(
            priorities, "(Запишите, что для вас важнее всего: сроки, надёжность, стоимость.)"
        ),
        "{{STACK}}": stack,
        "{{ALLOWED_PACKAGES}}": packages,
        "{{CHECK_COMMANDS}}": check_text,
        "{{TOOL_VERSIONS}}": tool_versions_text(languages),
        "{{STANDARD_VERSION}}": parch_ci.STANDARD_VERSION,
        "{{TARGET_OS}}": target_os,
    }
    for key, value in values.items():
        template = template.replace(key, value)
    if "powershell" in languages:
        template = template.rstrip("\n") + "\n" + PS_THRESHOLDS
    return template


def merge_permissions(report: Report) -> None:
    """Добавляет правила permissions в .claude/settings.json, не трогая остальные настройки."""
    rel = ".claude/settings.json"
    template = json.loads((TEMPLATES / "claude" / "settings.json").read_text(encoding="utf-8"))
    target = report.project / rel
    if not target.exists():
        report.write(rel, json.dumps(template, ensure_ascii=False, indent=2) + "\n")
        return
    try:
        current: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        report.skipped.append(rel)
        report.notes.append(
            f"{rel} не читается как JSON, правила permissions не добавлены: добавьте их вручную."
        )
        return
    permissions: dict[str, Any] = current.setdefault("permissions", {})
    changed = False
    for key in ("ask", "deny"):
        existing: list[str] = list(permissions.get(key, []))
        extra = [rule for rule in template["permissions"][key] if rule not in existing]
        if extra:
            permissions[key] = existing + extra
            changed = True
    if changed:
        target.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        report.created.append(f"{rel} (добавлены правила permissions)")
    else:
        report.skipped.append(rel)


def update_gitignore(report: Report, languages: list[str]) -> None:
    path = report.project / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    wanted = [
        *GITIGNORE_LINES,
        *(x for lang in languages for x in GITIGNORE_BY_LANGUAGE.get(lang, [])),
    ]
    missing = [line for line in wanted if line not in existing]
    if not missing:
        return
    text = "\n".join([*existing, *missing]) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    report.created.append(".gitignore (добавлены строки)" if existing else ".gitignore")


def create_typescript_files(project: Path, report: Report, name: str) -> None:
    """Файлы TypeScript-проекта; package-lock.json создаётся, если доступен npm."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"
    manifest = {
        "name": slug,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "main": "src/index.ts",
        "scripts": TS_SCRIPTS,
        "devDependencies": TS_TOOL_VERSIONS,
    }
    report.write("package.json", json.dumps(manifest, indent=2) + "\n")
    for template, target in (
        ("tsconfig.json", "tsconfig.json"),
        ("biome.json", "biome.json"),
        ("vitest.config.ts", "vitest.config.ts"),
        ("knip.json", "knip.json"),
        ("dependency-cruiser.json", ".dependency-cruiser.json"),
        ("smoke.test.ts", "tests/smoke.test.ts"),
    ):
        report.copy(f"typescript/{template}", target)
    npm = shutil.which("npm")
    if npm is None or (project / "package-lock.json").exists():
        if npm is None:
            report.notes.append(
                "npm не найден: выполните `npm install` один раз и закоммитьте package-lock.json "
                "(CI ставит зависимости командой `npm ci`, ей нужен lock-файл)."
            )
        return
    done = subprocess.run(
        [npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=project,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if done.returncode == 0:
        report.created.append("package-lock.json")
    else:
        report.notes.append(
            "Не удалось создать package-lock.json: выполните `npm install` и закоммитьте его."
        )


def create_csharp_files(project: Path, report: Report) -> None:
    """Файлы C#-проекта; решение и lock-файлы создаются, если доступен dotnet."""
    for template, target in (
        ("global.json", "global.json"),
        ("Directory.Build.props", "Directory.Build.props"),
        ("editorconfig", ".editorconfig"),
        ("App.Tests.csproj", "tests/App.Tests/App.Tests.csproj"),
        ("smoke.cs.txt", "tests/App.Tests/SmokeTests.cs"),
    ):
        report.copy(f"csharp/{template}", target)
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        report.notes.append(
            "dotnet не найден: установите .NET SDK версии из global.json, затем выполните "
            "`dotnet new sln -n App --format sln`, "
            "`dotnet sln add tests/App.Tests/App.Tests.csproj` и `dotnet restore`, "
            "закоммитьте решение и packages.lock.json "
            "(CI восстанавливает пакеты с --locked-mode)."
        )
        return
    env = {**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en", "DOTNET_NOLOGO": "1"}
    steps = (
        [dotnet, "new", "sln", "-n", "App", "--format", "sln"],
        [dotnet, "sln", "add", "tests/App.Tests/App.Tests.csproj"],
        [dotnet, "restore"],
    )
    if (project / "App.sln").exists():
        steps = steps[2:]
    for step in steps:
        done = subprocess.run(
            step, cwd=project, capture_output=True, env=env, check=False, timeout=600
        )
        if done.returncode != 0:
            report.notes.append(
                f"Не удалось выполнить `{' '.join(step[1:])}`: сделайте это вручную и "
                "закоммитьте App.sln и packages.lock.json."
            )
            return
    report.created.extend(["App.sln", "tests/App.Tests/packages.lock.json"])


def init_project(
    project: Path,
    name: str,
    languages: list[str],
    description: str,
    priorities: str,
    target_os: str,
    goal: Goal,
) -> dict[str, Any]:
    unknown = [lang for lang in languages if lang not in LANGUAGES]
    if unknown or not languages:
        raise ValueError(f"languages: выберите из {', '.join(LANGUAGES)}; получено {languages}")
    if not name.strip():
        raise ValueError("нужно название проекта (name)")
    existing_code = any(
        parch_ci.COLLECTORS[lang].has_sources(project)
        for lang in languages
        if lang in parch_ci.COLLECTORS
    )  # до того, как init что-либо создаст: код уже есть значит проект существующий
    project.mkdir(parents=True, exist_ok=True)
    report = Report(project)
    for doc in ("MODULES", "INTERFACES", "LESSONS", "QUESTIONS"):
        report.copy(f"docs/{doc}.md", f"docs/{doc}.md")
    report.copy("docs/adr/0000-template.md", "docs/adr/0000-template.md")
    report.copy("docs/INCIDENT_TEMPLATE.md", "docs/INCIDENT_TEMPLATE.md")
    report.copy("state/features.json", "state/features.json")
    report.copy("state/STATUS.md", "state/STATUS.md")
    report.write("docs/GOAL.md", render_goal(name, goal))
    report.write("docs/specs/.gitkeep", "")
    report.write("state/incidents/.gitkeep", "")
    if "python" in languages:
        report.write("pyproject.toml", PYPROJECT)
        report.write("requirements.txt", REQUIREMENTS)
        report.write("requirements-dev.txt", REQUIREMENTS_DEV)
        report.write("tests/test_smoke.py", SMOKE_TEST)
    if "typescript" in languages:
        create_typescript_files(project, report, name)
    if "csharp" in languages:
        create_csharp_files(project, report)
    if "powershell" in languages:
        report.copy("powershell/PSScriptAnalyzerSettings.psd1", "PSScriptAnalyzerSettings.psd1")
    if any(lang in CI_TEMPLATES for lang in languages):
        report.copy("ci/parch/parch_ci.py", CI_SCRIPT)
        report.copy("ci/parch/parch_status.py", ".github/parch/parch_status.py")
        report.copy("ci/parch/parch_catalog.py", ".github/parch/parch_catalog.py")
        report.copy("github/pull_request_template.md", ".github/pull_request_template.md")
        report.copy("ci/state.yml", ".github/workflows/state.yml")
        report.write(
            "state/baseline.json",
            json.dumps(initial_baseline(project, languages, existing_code), indent=2) + "\n",
        )
    for lang in languages:
        if lang in CI_TEMPLATES:
            workflow = "ci.yml" if lang == languages[0] or lang == "python" else f"ci-{lang}.yml"
            report.copy(f"ci/{CI_TEMPLATES[lang]}", f".github/workflows/{workflow}")
    merge_permissions(report)
    update_gitignore(report, languages)
    adr.build_index(project)
    report.created.append("docs/adr/README.md (индекс решений)")
    report.write(
        "docs/CONSTITUTION.md",
        render_constitution(name, description, priorities, languages, target_os),
    )
    report.notes.append(BUDGET_NOTE)
    return {
        "project": str(project),
        "created": report.created,
        "skipped_existing": report.skipped,
        "notes": report.notes,
    }


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    request: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    languages = [str(x).lower() for x in request.get("languages", [])]
    result = init_project(
        Path(str(request.get("project_dir") or ".")).resolve(),
        str(request.get("name", "")),
        languages,
        str(request.get("description", "")),
        str(request.get("priorities", "")),
        normalize_target_os(request.get("target_os")),
        parse_goal(request.get("goal")),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError, shutil.Error) as error:
        sys.stderr.write(f"init-project: {error}\n")
        sys.exit(1)
