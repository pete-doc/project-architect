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
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        "C# (.NET, nullable, warnings as errors, xUnit)",
        "nuget",
        "xunit, xunit.runner.visualstudio, Microsoft.NET.Test.Sdk, coverlet.collector",
        (),
    ),
    "powershell": Language(
        "PowerShell (PSScriptAnalyzer, Pester)",
        "psgallery",
        "Pester, PSScriptAnalyzer",
        (),
    ),
}
CI_TEMPLATES = {"python": "python.yml", "typescript": "typescript.yml"}
GITIGNORE_LINES = [".claude/audit/", ".claude/settings.local.json"]

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


def initial_baseline(project: Path, languages: list[str]) -> dict[str, Any]:
    """Начальный baseline: известные тесты, пропуски, подавления и настройки каждого языка."""
    first_tests = {
        "python": ["tests/test_smoke.py::test_smoke"],
        "typescript": ["smoke.test.ts::smoke"],
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
    for lang in (item for item in languages if item in first_tests):
        rules = parch_ci.RULES[lang]
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


def render_constitution(name: str, description: str, priorities: str, languages: list[str]) -> str:
    template = (TEMPLATES / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    chosen = [LANGUAGES[lang] for lang in languages]
    stack = "\n".join(f"- {item.title}" for item in chosen)
    packages = "\n".join(f"- {item.ecosystem}: {item.packages}" for item in chosen)
    commands = [cmd for item in chosen for cmd in item.checks]
    pending = [lang for lang in languages if not LANGUAGES[lang].checks]
    check_text = "\n".join(f"- {cmd}" for cmd in commands)
    if pending:
        names = ", ".join(pending)
        check_text += ("\n\n" if check_text else "") + (
            f"Команды проверки для {names} появятся вместе с CI-шаблоном для этого языка."
        )
    values = {
        "{{PROJECT_NAME}}": name,
        "{{DESCRIPTION}}": description.strip() or "(Опишите проект одной-двумя фразами.)",
        "{{PRIORITIES}}": bullets(
            priorities, "(Запишите, что для вас важнее всего: сроки, надёжность, стоимость.)"
        ),
        "{{STACK}}": stack,
        "{{ALLOWED_PACKAGES}}": packages,
        "{{CHECK_COMMANDS}}": check_text,
    }
    for key, value in values.items():
        template = template.replace(key, value)
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


def update_gitignore(report: Report) -> None:
    path = report.project / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
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


def init_project(
    project: Path, name: str, languages: list[str], description: str, priorities: str
) -> dict[str, Any]:
    unknown = [lang for lang in languages if lang not in LANGUAGES]
    if unknown or not languages:
        raise ValueError(f"languages: выберите из {', '.join(LANGUAGES)}; получено {languages}")
    if not name.strip():
        raise ValueError("нужно название проекта (name)")
    project.mkdir(parents=True, exist_ok=True)
    report = Report(project)
    for doc in ("MODULES", "INTERFACES", "LESSONS", "QUESTIONS"):
        report.copy(f"docs/{doc}.md", f"docs/{doc}.md")
    report.copy("docs/adr/0000-template.md", "docs/adr/0000-template.md")
    report.copy("state/features.json", "state/features.json")
    report.copy("state/STATUS.md", "state/STATUS.md")
    if "python" in languages:
        report.write("pyproject.toml", PYPROJECT)
        report.write("requirements.txt", REQUIREMENTS)
        report.write("requirements-dev.txt", REQUIREMENTS_DEV)
        report.write("tests/test_smoke.py", SMOKE_TEST)
    if "typescript" in languages:
        create_typescript_files(project, report, name)
    if "python" in languages or "typescript" in languages:
        report.copy("ci/parch/parch_ci.py", CI_SCRIPT)
        report.write(
            "state/baseline.json", json.dumps(initial_baseline(project, languages), indent=2) + "\n"
        )
    for lang in languages:
        if lang in CI_TEMPLATES:
            workflow = "ci.yml" if lang == languages[0] or lang == "python" else f"ci-{lang}.yml"
            report.copy(f"ci/{CI_TEMPLATES[lang]}", f".github/workflows/{workflow}")
        else:
            report.notes.append(
                f"CI-шаблона для {lang} пока нет (появится в фазе D): создана только запись "
                "в CONSTITUTION.md."
            )
    merge_permissions(report)
    update_gitignore(report)
    adr.build_index(project)
    report.created.append("docs/adr/README.md (индекс решений)")
    report.write(
        "docs/CONSTITUTION.md", render_constitution(name, description, priorities, languages)
    )
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
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError, shutil.Error) as error:
        sys.stderr.write(f"init-project: {error}\n")
        sys.exit(1)
