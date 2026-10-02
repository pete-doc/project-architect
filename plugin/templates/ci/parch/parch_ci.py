"""Проверки CI ProjectArchitect (общие для всех языков).

Только стандартная библиотека. В проект копируется как .github/parch/parch_ci.py.
Запуск из корня проекта:  python .github/parch/parch_ci.py <проверка> [--language python]

Проверки:
  tests         число тестов не уменьшилось: каждый тест из baseline на месте
  modules       каждый модуль кода есть в docs/MODULES.md
  deps          каждый пакет из манифестов есть в разделе «Разрешённые пакеты» CONSTITUTION.md
  dead-code     мёртвый код и лишние зависимости (vulture, deptry), «храповик» по baseline
  duplicates    дубли кода (jscpd), «храповик» по baseline
  architecture  правила архитектуры (import-linter); правило, которое ничего не охватывает, падает
  coverage      покрытие тестами не ниже baseline
  skips         пропущенные тесты (skip, xfail, Ignore, -Skip) считаются удалёнными
  suppressions  подавляющие комментарии (type: ignore, noqa, ts-ignore): рост запрещён
  settings      настройки проверок (ruff, pyright, tsconfig, eslint...) не менялись
  baseline      обновить baseline (--update); каждое ухудшение требует флага владельца:
                --accept-new, --accept-removed, --accept-skips,
                --accept-suppressions, --accept-config

«Храповик»: старые нарушения (они записаны в baseline) CI пропускает, любое новое останавливает.
Baseline лежит в state/baseline.json и state/jscpd-baseline.json; менять его может только владелец.
Код выхода: 0 всё хорошо; 1 нарушение или сломавшаяся проверка (молча «зелёным» не считается).
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tokenize
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

BASELINE_FILE = "state/baseline.json"
JSCPD_BASELINE_FILE = "state/jscpd-baseline.json"
JSCPD_VERSION = "5.4.0"
JSCPD_MIN_LINES = "6"
JSCPD_MIN_TOKENS = "60"
VULTURE_MIN_CONFIDENCE = "60"
VULTURE_EXCLUDE = "*/.github/*,*/.venv/*,*/venv/*,*/node_modules/*,*/docs/*,*/state/*,*/.claude/*"
CONSTITUTIONS = ("docs/CONSTITUTION.md", "CONSTITUTION.md")
SKIP_DIRS = {
    ".git", ".github", ".claude", ".venv", "venv", "env", "node_modules", "__pycache__",
    "build", "dist", "docs", "state", "tests", "test", "tools", "analysis", ".tox",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "site-packages", "coverage",
}  # fmt: skip
MAX_SHOWN = 20
ECOSYSTEM_ALIASES = {
    "pip": "pip", "pypi": "pip", "python": "pip",
    "npm": "npm", "node": "npm", "ts": "npm", "typescript": "npm",
    "nuget": "nuget", "dotnet": "nuget", "csharp": "nuget", "c#": "nuget",
    "psgallery": "psgallery", "powershell": "psgallery",
}  # fmt: skip


@dataclass
class Result:
    ok: bool = True
    lines: list[str] = field(default_factory=list[str])

    def fail(self, *lines: str) -> None:
        self.ok = False
        self.lines.extend(lines)

    def note(self, *lines: str) -> None:
        self.lines.extend(lines)


class ToolError(Exception):
    """Инструмент проверки не запустился или сломался: это тоже провал, а не «зелёное»."""


# ---------- помощники ----------


def as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return {}


def as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


def as_strings(value: object) -> list[str]:
    return [str(item) for item in as_list(value)]


def shown(items: list[str]) -> list[str]:
    head = [f"  - {item}" for item in items[:MAX_SHOWN]]
    extra = [f"  ... и ещё {len(items) - MAX_SHOWN}"] if len(items) > MAX_SHOWN else []
    return head + extra


def normalize_path(text: str) -> str:
    cleaned = text.strip().strip("`").replace("\\", "/")
    return PurePosixPath(cleaned).as_posix().removeprefix("./").rstrip("/")


def read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return as_dict(json.loads(path.read_text(encoding="utf-8")))


def read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return as_dict(tomllib.loads(path.read_text(encoding="utf-8")))


def run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "PYTHONUTF8": "1",
                **(env or {}),
            },  # UTF-8 в дочерних Python (Windows)
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError(f"не удалось запустить {' '.join(argv[:3])}: {error}") from error


def python_tool(module: str) -> list[str]:
    return [sys.executable, "-m", module]


def tail(done: subprocess.CompletedProcess[str], limit: int = 1500) -> str:
    return (done.stdout + done.stderr)[-limit:]


# ---------- baseline ----------


class Baseline:
    """state/baseline.json: известные нарушения и тесты, записанные владельцем."""

    def __init__(self, project: Path) -> None:
        self.path = project / BASELINE_FILE
        self.data = read_json(self.path)

    def section(self, name: str, language: str) -> object:
        return as_dict(self.data.get(name)).get(language)

    def strings(self, name: str, language: str) -> set[str]:
        return set(as_strings(self.section(name, language)))

    def number(self, name: str, language: str) -> float | None:
        value = self.section(name, language)
        return float(value) if isinstance(value, (int, float)) else None

    def has(self, name: str, language: str) -> bool:
        return self.section(name, language) is not None

    def set(self, name: str, language: str, value: object) -> None:
        part = as_dict(self.data.get(name))
        part[language] = value
        self.data[name] = part
        self.data["version"] = 1

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.path.write_text(text, encoding="utf-8", newline="\n")


# ---------- Python: исходники, модули ----------


def py_source_roots(project: Path) -> list[str]:
    """Корни кода: [tool.parch] source_roots в pyproject.toml, иначе `src`, иначе корень."""
    parch = as_dict(as_dict(read_toml(project / "pyproject.toml").get("tool")).get("parch"))
    configured = as_strings(parch.get("source_roots"))
    if configured:
        return [normalize_path(x) for x in configured]
    return ["src"] if (project / "src").is_dir() else ["."]


def py_modules(project: Path) -> list[str]:
    """Модули: пакеты и одиночные .py-файлы прямо в корне кода (пути от корня проекта)."""
    modules: list[str] = []
    for root in py_source_roots(project):
        base = project / root
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            rel = normalize_path(entry.relative_to(project).as_posix())
            if entry.is_dir():
                skip = entry.name in SKIP_DIRS or entry.name.startswith(".")
                if not skip and ((entry / "__init__.py").is_file() or any(entry.rglob("*.py"))):
                    modules.append(rel)
            elif entry.suffix == ".py" and entry.name not in {
                "__init__.py", "setup.py", "conftest.py", "noxfile.py"
            }:  # fmt: skip
                modules.append(rel)
    return modules


def py_module_names(project: Path) -> list[str]:
    return [PurePosixPath(m).stem if m.endswith(".py") else PurePosixPath(m).name
            for m in py_modules(project)]  # fmt: skip


def py_has_sources(project: Path) -> bool:
    return bool(py_modules(project))


# ---------- tests ----------


def py_test_ids(project: Path, report: Path | None = None) -> list[str]:
    """Тесты Python собираются самим pytest; отчёт не нужен."""
    del report
    argv = [*python_tool("pytest"), "--collect-only", "-q", "-p", "no:cacheprovider"]
    done = run(argv, project)
    if done.returncode not in (0, 5):
        raise ToolError("pytest не смог собрать тесты:\n" + tail(done))
    lines = (line.strip() for line in done.stdout.splitlines())
    return sorted({line for line in lines if "::" in line and " " not in line})


def check_tests(project: Path, language: str, report: Path | None = None) -> Result:
    result = Result()
    current = set(COLLECTORS[language].test_ids(project, report))
    baseline = Baseline(project)
    known = baseline.strings("tests", language)
    if not current:
        result.fail("В проекте нет ни одного теста: пустой набор тестов не считается «зелёным».")
        return result
    removed = sorted(known - current)
    if removed:
        result.fail(
            f"Число тестов уменьшилось: в baseline {len(known)}, найдено {len(current)}. "
            "Эти тесты исчезли (удалены или переименованы):",
            *shown(removed),
            "Тест нельзя удалять, чтобы «починить» проверки. Если удаление настоящее, его "
            "утверждает владелец: python .github/parch/parch_ci.py baseline --update "
            "--accept-removed",
        )
        return result
    result.note(f"Тестов: {len(current)} (в baseline {len(known)}, новых {len(current - known)}).")
    if not baseline.has("tests", language):
        result.note(
            "Внимание: в baseline нет списка тестов, удаление тестов не будет замечено. "
            "Создайте его: baseline --update."
        )
    return result


# ---------- modules ----------


def modules_table(project: Path) -> list[str]:
    path = project / "docs" / "MODULES.md"
    if not path.is_file():
        raise ToolError("нет docs/MODULES.md: карта модулей обязательна")
    paths: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        header = cells[1].lower() in {"путь", "path"} if len(cells) >= 2 else False
        if len(cells) >= 2 and cells[1] and not header and not set(cells[1]) <= set("-: "):
            paths.append(normalize_path(cells[1]))
    return paths


def check_modules(project: Path, language: str) -> Result:
    result = Result()
    found = COLLECTORS[language].modules(project)
    listed = set(modules_table(project))
    missing = [m for m in found if normalize_path(m) not in listed]
    if missing:
        result.fail(
            "Эти модули есть в коде, но не записаны в docs/MODULES.md (колонка «Путь»):",
            *shown(missing),
            "Добавьте строку в таблицу: модуль, путь, назначение, язык, статус active.",
        )
    else:
        result.note(f"Модулей в коде: {len(found)}, все есть в docs/MODULES.md.")
    stale = sorted(p for p in listed if p and not (project / p).exists())
    if stale:
        result.note(
            "Внимание: в MODULES.md записаны несуществующие пути (устарели):", *shown(stale)
        )
    return result


# ---------- deps ----------


def norm_name(ecosystem: str, name: str) -> str:
    name = name.strip().strip("`\"'")
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pip" else name.lower()


def load_allowed(project: Path) -> dict[str, set[str]]:
    constitution = next((project / c for c in CONSTITUTIONS if (project / c).is_file()), None)
    if constitution is None:
        raise ToolError("нет CONSTITUTION.md: список разрешённых пакетов негде взять")
    heading = re.compile(
        r"^#{1,6}\s*(разрешённые пакеты|разрешенные пакеты|allowed packages)\s*$", re.IGNORECASE
    )
    allowed: dict[str, set[str]] = {}
    inside = False
    for line in constitution.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if heading.match(stripped):
            inside = True
        elif inside and re.match(r"^#{1,6}\s", stripped):
            break
        elif inside and stripped.startswith(("-", "*")):
            label, _, names = stripped.lstrip("-* ").partition(":")
            ecosystem = ECOSYSTEM_ALIASES.get(label.strip().strip("`").lower())
            if ecosystem:
                bucket = allowed.setdefault(ecosystem, set())
                bucket.update(norm_name(ecosystem, n) for n in names.split(",") if n.strip())
    return allowed


def py_req_name(spec: str) -> str | None:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec.strip())
    return match.group(1) if match else None


def read_requirements(path: Path, seen: set[Path] | None = None) -> list[tuple[str, str]]:
    seen = seen if seen is not None else set()
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    found: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            found += read_requirements(path.parent / line.split(None, 1)[1].strip(), seen)
        elif line.startswith(("-e ", "--editable ")):
            target = line.split(None, 1)[1].strip()
            if "://" in target or target.startswith("git+"):
                found.append((path.name, target))
        elif line.startswith("-"):
            continue  # параметры pip: --index-url и другие
        elif "://" in line or line.startswith("git+") or line.endswith((".whl", ".zip", ".gz")):
            found.append((path.name, line))
        else:
            found.append((path.name, py_req_name(line) or line))
    return found


def pyproject_specs(config: dict[str, object]) -> list[str]:
    specs: list[str] = []
    project = as_dict(config.get("project"))
    specs += as_strings(project.get("dependencies"))
    for group in as_dict(project.get("optional-dependencies")).values():
        specs += as_strings(group)
    for group in as_dict(config.get("dependency-groups")).values():
        specs += [s for s in as_list(group) if isinstance(s, str)]
    poetry = as_dict(as_dict(config.get("tool")).get("poetry"))
    for key in ("dependencies", "dev-dependencies"):
        specs += [k for k in as_dict(poetry.get(key)) if k.lower() != "python"]
    for group in as_dict(poetry.get("group")).values():
        specs += list(as_dict(as_dict(group).get("dependencies")))
    return specs


def py_manifest_packages(project: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(project.glob("requirements*.txt")):
        found += read_requirements(path)
    specs = pyproject_specs(read_toml(project / "pyproject.toml"))
    return found + [("pyproject.toml", py_req_name(s) or s) for s in specs]


def check_deps(project: Path, language: str) -> Result:
    result = Result()
    allowed = load_allowed(project)
    collector = COLLECTORS[language]
    known = allowed.get(collector.ecosystem, set())
    bad = [
        f"{name} (в {source})"
        for source, name in collector.manifest_packages(project)
        if norm_name(collector.ecosystem, name) not in known
    ]
    if bad:
        result.fail(
            f"Пакеты не из списка «Разрешённые пакеты» в CONSTITUTION.md ({collector.ecosystem}):",
            *shown(sorted(set(bad))),
            "Новый пакет сначала оформляется как ADR (/parch:adr) и ждёт утверждения владельца; "
            "после этого владелец добавляет его в список.",
        )
    else:
        result.note("Все пакеты из манифестов есть в списке разрешённых.")
    return result


# ---------- dead-code ----------

_VULTURE_LINE = re.compile(r"^(.+?):\d+: unused (\w[\w ]*?) '(.+?)' \(\d+% confidence\)")


def vulture_findings(project: Path) -> set[str]:
    roots = [r for r in py_source_roots(project) if (project / r).exists()]
    paths = roots + (["tests"] if (project / "tests").is_dir() else [])
    argv = [*python_tool("vulture"), *paths, "--min-confidence", VULTURE_MIN_CONFIDENCE]
    argv += ["--exclude", VULTURE_EXCLUDE]
    if (project / "state" / "vulture-whitelist.py").is_file():
        argv.append("state/vulture-whitelist.py")
    done = run(argv, project)
    if done.returncode not in (0, 3):
        raise ToolError("vulture не отработал:\n" + tail(done))
    found: set[str] = set()
    for line in done.stdout.splitlines():
        match = _VULTURE_LINE.match(line.strip())
        if match:
            found.add(f"vulture|{normalize_path(match.group(1))}|{match.group(2)}|{match.group(3)}")
    return found


def deptry_findings(project: Path) -> set[str]:
    report_file = project / ".deptry-report.json"
    argv = [*python_tool("deptry"), ".", "--no-ansi", "--json-output", report_file.name]
    for name in py_module_names(project):
        argv += ["--known-first-party", name]
    for option, name in (
        ("--requirements-files", "requirements.txt"),
        ("--requirements-files-dev", "requirements-dev.txt"),
    ):
        if (project / name).is_file():
            argv += [option, name]
    done = run(argv, project)
    try:
        if done.returncode not in (0, 1):
            raise ToolError("deptry не отработал:\n" + tail(done))
        text = report_file.read_text(encoding="utf-8") if report_file.is_file() else "[]"
        report = as_list(json.loads(text))
    finally:
        report_file.unlink(missing_ok=True)
    found: set[str] = set()
    for item in report:
        entry = as_dict(item)
        code = as_dict(entry.get("error")).get("code", "")
        file = as_dict(entry.get("location")).get("file", "")
        found.add(f"deptry|{normalize_path(str(file))}|{code}|{entry.get('module', '')}")
    return found


def py_dead_code(project: Path) -> set[str]:
    return vulture_findings(project) | deptry_findings(project)


def describe_finding(fingerprint: str) -> str:
    parts = fingerprint.split("|")
    if parts[0] == "vulture":
        return f"{parts[1]}: неиспользуемый {parts[2]} «{parts[3]}»"
    return f"{parts[1] or 'манифест'}: {parts[2]}, зависимость «{parts[3]}»"


def check_dead_code(project: Path, language: str) -> Result:
    result = Result()
    current = COLLECTORS[language].dead_code(project)
    known = Baseline(project).strings("dead_code", language)
    new = sorted(current - known)
    if new:
        result.fail(
            f"Новый мёртвый код или лишние зависимости ({len(new)}), в baseline их нет:",
            *shown([describe_finding(f) for f in new]),
            "Удалите их. Старые находки из baseline CI пропускает, новые нет («храповик»).",
        )
        return result
    result.note(f"Мёртвый код: новых нет (всего {len(current)}, известных {len(current & known)}).")
    gone = len(known - current)
    if gone:
        result.note(f"Улучшение: исправлено старых находок: {gone}. Закрепите: baseline --update.")
    return result


# ---------- duplicates ----------


def jscpd_command() -> list[str]:
    override = os.environ.get("PARCH_JSCPD")
    return shlex.split(override) if override else ["npx", "--yes", f"jscpd@{JSCPD_VERSION}"]


def source_roots(project: Path, language: str) -> list[str]:
    """Корни кода проекта для языка (для остальных языков пока весь проект)."""
    if language == "python":
        return py_source_roots(project)
    if language == "typescript":
        return ts_source_roots(project)
    return ["."]


def jscpd_args(project: Path, language: str) -> list[str]:
    roots = [r for r in source_roots(project, language) if (project / r).exists()]
    ignored = ["tests", ".github", "docs", "state", "node_modules", ".venv", "__pycache__"]
    ignore = ",".join(f"**/{name}/**" for name in ignored)
    return [
        *roots,
        *("--format", COLLECTORS[language].jscpd_format),
        *("--min-lines", JSCPD_MIN_LINES),
        *("--min-tokens", JSCPD_MIN_TOKENS),
        *("--ignore", ignore),
        *("--reporters", "console"),
        *("--baseline", JSCPD_BASELINE_FILE),
    ]


def ensure_empty_jscpd_baseline(project: Path) -> None:
    path = project / JSCPD_BASELINE_FILE
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{\n  "version": 1,\n  "fingerprints": {}\n}\n', encoding="utf-8", newline="\n"
        )


def check_duplicates(project: Path, language: str) -> Result:
    result = Result()
    if not COLLECTORS[language].has_sources(project):
        result.note("Кода пока нет, проверка дублей пропущена.")
        return result
    ensure_empty_jscpd_baseline(project)
    extra = ["--fail-on-new-clones", "0", "--fail-on-empty"]
    done = run([*jscpd_command(), *jscpd_args(project, language), *extra], project)
    output = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr).strip()
    if done.returncode != 0:
        result.fail(
            "Найдены новые дубли кода (или jscpd не отработал):",
            *output.splitlines()[-25:],
            "Вынесите повторяющийся код в общую функцию. Старые дубли из baseline CI "
            "пропускает, новые нет («храповик»).",
        )
    else:
        result.note("Новых дублей кода нет.")
    return result


# ---------- architecture ----------

IMPORTLINTER_EXAMPLE = (
    "Пример .importlinter в корне проекта:\n"
    "  [importlinter]\n  root_package = ИМЯ_ПАКЕТА\n\n"
    "  [importlinter:contract:ui-not-db]\n  name = UI не обращается к хранилищу напрямую\n"
    "  type = forbidden\n  source_modules = ИМЯ_ПАКЕТА.ui\n  forbidden_modules = ИМЯ_ПАКЕТА.db\n"
    "  allow_indirect_imports = True"
)


@dataclass
class ArchConfig:
    roots: list[str]
    contracts: list[dict[str, str]]


def split_names(raw: str) -> list[str]:
    return [n for n in re.split(r"[\s,|]+", raw) if n]


def contract_text(contract: dict[str, object]) -> dict[str, str]:
    return {
        key: "\n".join(as_strings(value)) if as_list(value) else str(value)
        for key, value in contract.items()
    }


def importlinter_config(project: Path) -> ArchConfig | None:
    """Корневые пакеты и контракты из .importlinter или pyproject.toml; None, если конфига нет."""
    ini = project / ".importlinter"
    if ini.is_file():
        parser = configparser.ConfigParser()
        parser.read(ini, encoding="utf-8")
        raw = ""
        if parser.has_section("importlinter"):
            raw = parser.get("importlinter", "root_packages", fallback="") or parser.get(
                "importlinter", "root_package", fallback=""
            )
        contracts = [
            dict(parser.items(s))
            for s in parser.sections()
            if s.startswith("importlinter:contract:")
        ]
        return ArchConfig(split_names(raw), contracts)
    config = as_dict(as_dict(read_toml(project / "pyproject.toml").get("tool")).get("importlinter"))
    if not config:
        return None
    roots = as_strings(config.get("root_packages")) or as_strings([config.get("root_package")])
    contracts = [contract_text(as_dict(item)) for item in as_list(config.get("contracts"))]
    return ArchConfig([r for r in roots if r and r != "None"], contracts)


def contract_modules(contract: dict[str, str]) -> list[str]:
    """Все модули, названные в контракте (слои дополняются именем контейнера)."""
    names: list[str] = []
    for key in ("source_modules", "forbidden_modules", "modules"):
        names += split_names(contract.get(key, ""))
    containers = split_names(contract.get("containers", ""))
    for layer in split_names(contract.get("layers", "")):
        names += [f"{c}.{layer}" for c in containers] if containers else [layer]
    return [n for n in names if "*" not in n]


def unresolved_modules(project: Path, names: list[str]) -> list[str]:
    """Модули из контрактов, которых нет ни в коде, ни среди установленных пакетов.

    import-linter сам молча пропускает опечатку в `forbidden_modules`: правило «проходит», ничего
    не проверяя. Поэтому каждое имя дополнительно проверяется на существование.
    """
    paths = [str((project / r).resolve()) for r in py_source_roots(project)]
    probe = (
        "import importlib.util, json, sys\n"
        "missing = []\n"
        "for name in json.loads(sys.argv[1]):\n"
        "    try:\n"
        "        found = importlib.util.find_spec(name) is not None\n"
        "    except (ImportError, ValueError):\n"
        "        found = False\n"
        "    if not found:\n"
        "        missing.append(name)\n"
        "print(json.dumps(missing))\n"
    )
    env = {"PYTHONPATH": os.pathsep.join([*paths, os.environ.get("PYTHONPATH", "")])}
    done = run([sys.executable, "-c", probe, json.dumps(sorted(set(names)))], project, env)
    if done.returncode != 0:
        raise ToolError("не удалось проверить имена модулей в контрактах:\n" + tail(done))
    return as_strings(json.loads(done.stdout.strip().splitlines()[-1]))


def check_architecture(project: Path, language: str) -> Result:
    return COLLECTORS[language].architecture(project)


def py_architecture(project: Path) -> Result:
    result = Result()
    names = py_module_names(project) if py_has_sources(project) else []
    if not names:
        result.note("Кода пока нет, проверка архитектуры пропущена.")
        return result
    config = importlinter_config(project)
    if config is None:
        result.fail(
            "Для модулей кода нет правил архитектуры (.importlinter).", IMPORTLINTER_EXAMPLE
        )
        return result
    if not config.contracts:
        result.fail(
            "В .importlinter нет ни одного контракта: правило, которого нет, ничего не проверяет.",
            IMPORTLINTER_EXAMPLE,
        )
        return result
    uncovered = [n for n in names if n not in config.roots]
    if uncovered:
        result.fail(
            "Эти модули не охвачены правилами архитектуры (нет в root_package(s) .importlinter):",
            *shown(uncovered),
        )
        return result
    named = [m for contract in config.contracts for m in contract_modules(contract)]
    missing = unresolved_modules(project, named)
    if missing:
        result.fail(
            "В правилах архитектуры названы модули, которых нет (опечатка в имени?). Такое правило "
            "«проходит», ничего не проверяя, поэтому оно считается ошибкой:",
            *shown(missing),
        )
        return result
    paths = [str((project / r).resolve()) for r in py_source_roots(project)]
    env = {"PYTHONPATH": os.pathsep.join([*paths, os.environ.get("PYTHONPATH", "")])}
    launcher = "from importlinter.cli import lint_imports_command; lint_imports_command()"
    done = run([sys.executable, "-c", launcher], project, env)
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0:
        result.fail(
            "Правила архитектуры нарушены (или не могут быть проверены):",
            *output.splitlines()[-25:],
        )
    elif not re.search(r"Contracts: [1-9]\d* kept", output):
        result.fail(
            "import-linter не проверил ни одного контракта: правило «пусто-зелёное».",
            *output.splitlines()[-10:],
        )
    else:
        result.note(f"Правила архитектуры соблюдены (контрактов: {len(config.contracts)}).")
    return result


# ---------- coverage ----------


def py_coverage(project: Path) -> float:
    sources = ",".join(py_module_names(project))
    xml = project / "coverage.xml"
    run_argv = [*python_tool("coverage"), "run", f"--source={sources}"]
    done = run([*run_argv, "-m", "pytest", "-q", "-p", "no:cacheprovider"], project)
    if done.returncode != 0:
        raise ToolError("тесты под coverage не прошли:\n" + tail(done))
    run([*python_tool("coverage"), "xml", "-o", xml.name], project)
    try:
        return float(ElementTree.parse(xml).getroot().attrib["line-rate"]) * 100
    finally:
        xml.unlink(missing_ok=True)
        (project / ".coverage").unlink(missing_ok=True)


def check_coverage(project: Path, language: str) -> Result:
    result = Result()
    if not COLLECTORS[language].has_sources(project):
        result.note("Кода пока нет, покрытие не считается.")
        return result
    current = COLLECTORS[language].coverage(project)
    floor = Baseline(project).number("coverage", language)
    if floor is None:
        result.note(
            f"Покрытие {current:.1f}%. В baseline порога нет: зафиксируйте его командой "
            "baseline --update."
        )
    elif current + 1e-9 < floor:
        result.fail(
            f"Покрытие упало: {current:.1f}% при пороге {floor:.1f}% (baseline). "
            "Добавьте тесты на новый код."
        )
    else:
        result.note(f"Покрытие {current:.1f}% (порог {floor:.1f}%).")
    return result


# ---------- baseline --update ----------


@dataclass(frozen=True)
class Accept:
    """Решения владельца, без которых baseline не принимает ухудшений."""

    new: bool = False
    removed: bool = False
    skips: bool = False
    suppressions: bool = False
    config: bool = False


def refuse_growth(
    result: Result,
    baseline: Baseline,
    section: str,
    language: str,
    current: dict[str, int],
    allowed: bool,
    flag: str,
    title: str,
) -> None:
    if baseline.has(section, language) and not allowed:
        more = grown(current, count_dict(baseline.section(section, language)))
        if more:
            result.fail(
                f"Отказ: {title} выросло, записывать это в baseline нельзя ({flag}):", *shown(more)
            )


def update_baseline(
    project: Path, language: str, accept: Accept, report: Path | None = None
) -> Result:
    result = Result()
    baseline = Baseline(project)
    outcomes = test_outcomes(project, language, report)
    if outcomes is None:
        result.fail(
            f"Нет отчёта о запуске тестов для {language}: без него пропуски по фактическому "
            "результату не посчитать. Передайте --report ФАЙЛ.",
            REPORT_HINT.get(language, ""),
        )
        return result
    skipped_now = skipped_ids(outcomes)
    new_skipped = sorted(skipped_now - baseline.strings("skipped_tests", language))
    if baseline.has("skipped_tests", language) and new_skipped and not accept.skips:
        result.fail(
            "Отказ: появились тесты, пропущенные по фактическому результату запуска, "
            "записывать их в baseline нельзя (--accept-skips):",
            *shown(new_skipped),
        )
    skips = scan_counts(project, language, RULES[language].skip_patterns, True)
    suppressions = scan_counts(
        project, language, RULES[language].suppression_patterns, False, in_comments=True
    )
    config = settings_fingerprint(project, language)
    refuse_growth(
        result,
        baseline,
        "skips",
        language,
        skips,
        accept.skips,
        "--accept-skips",
        "число пропущенных тестов",
    )
    refuse_growth(
        result, baseline, "suppressions", language, suppressions, accept.suppressions,
        "--accept-suppressions", "число подавляющих комментариев",
    )  # fmt: skip
    if baseline.has("config", language) and not accept.config:
        known = as_dict(baseline.section("config", language))
        changed = sorted(
            {k for k in config if known.get(k) != config[k]} | (set(known) - set(config))
        )
        if changed:
            result.fail(
                "Отказ: настройки проверок изменены, записывать их в baseline нельзя "
                "(--accept-config):",
                *shown(changed),
            )
    collected = COLLECTORS.get(language)
    tests: set[str] = set()
    dead: set[str] = set()
    if collected is not None:
        tests = set(collected.test_ids(project, report))
        removed = sorted(baseline.strings("tests", language) - tests)
        if removed and not accept.removed:
            result.fail(
                "Отказ: из baseline пропали бы тесты. Их удаление должен утвердить владелец "
                "(флаг --accept-removed):",
                *shown(removed),
            )
        has_sources = collected.has_sources(project)
        dead = collected.dead_code(project) if has_sources else set[str]()
        initialized = baseline.has("dead_code", language)
        new_dead = sorted(dead - baseline.strings("dead_code", language))
        if new_dead and initialized and not accept.new:
            result.fail(
                "Отказ: это новые нарушения, записывать их в baseline без решения владельца нельзя "
                "(флаг --accept-new):",
                *shown([describe_finding(f) for f in new_dead]),
            )
        if has_sources:
            ensure_empty_jscpd_baseline(project)
            probe = [*jscpd_command(), *jscpd_args(project, language), "--fail-on-new-clones", "0"]
            if run(probe, project).returncode != 0 and initialized and not accept.new:
                result.fail(
                    "Отказ: в коде есть новые дубли, записывать их в baseline нельзя "
                    "(--accept-new)."
                )
    if not result.ok:
        return result
    baseline.set("skips", language, skips)
    baseline.set("skipped_tests", language, sorted(skipped_now))
    baseline.set("suppressions", language, suppressions)
    baseline.set("config", language, config)
    if collected is not None:
        baseline.set("tests", language, sorted(tests))
        baseline.set("dead_code", language, sorted(dead))
        if collected.has_sources(project):
            baseline.set("coverage", language, round(collected.coverage(project), 2))
            refresh = [*jscpd_command(), *jscpd_args(project, language), "--update-baseline"]
            updated = run(refresh, project)
            if updated.returncode != 0:
                raise ToolError("jscpd не смог обновить baseline:\n" + tail(updated, 800))
    baseline.save()
    result.note(
        f"baseline обновлён: тестов {len(tests)}, находок мёртвого кода {len(dead)}, "
        f"пропусков {sum(skips.values())}, подавлений {sum(suppressions.values())}, "
        f"отпечатков настроек {len(config)}."
    )
    return result


# ---------- правила для языков: пропуски тестов, подавления, файлы настроек ----------


@dataclass(frozen=True)
class LanguageRules:
    source_extensions: tuple[str, ...]
    test_file_patterns: tuple[str, ...]  # fnmatch по пути от корня проекта (прямые слэши)
    skip_patterns: dict[str, str]  # вид пропуска -> регулярное выражение
    suppression_patterns: dict[str, str]  # вид подавления -> регулярное выражение
    config_files: tuple[str, ...]  # файлы настроек проверок целиком (fnmatch по имени файла)
    container_files: tuple[str, ...]  # файлы, где настройки проверок лежат в разделах
    code_kinds: tuple[str, ...] = ()  # виды подавлений, которые ищутся в коде, а не в комментариях


RULES: dict[str, LanguageRules] = {
    "python": LanguageRules(
        source_extensions=(".py",),
        test_file_patterns=("test_*.py", "*_test.py", "conftest.py"),
        skip_patterns={
            "mark.skip": r"\bmark\.(skip|skipif|xfail)\b",
            "pytest.skip()": r"\bpytest\.(skip|xfail|importorskip)\s*\(",
            "unittest.skip": r"\bunittest\.(skip|skipIf|skipUnless|expectedFailure)\b",
            "skipTest": r"\bskipTest\s*\(|\bSkipTest\b",
            "@skip": r"^\s*@(skip|skipIf|skipUnless|expectedFailure)\b",
            "add_marker(skip)": r"\badd_marker\s*\(.*\b(skip|xfail)\b",
        },
        suppression_patterns={
            "type: ignore": r"#\s*type:\s*ignore",
            "pyright: ignore": r"#\s*pyright:\s*ignore",
            "pyright: basic/off": r"#\s*pyright:\s*(basic|standard|off)\b",
            "noqa": r"#\s*(ruff:\s*|flake8:\s*)?noqa\b",
            "pragma: no cover": r"#\s*pragma:\s*no\s*cover",
            "mypy: ignore-errors": r"#\s*mypy:\s*ignore-errors",
        },
        config_files=(
            "ruff.toml",
            ".ruff.toml",
            "pyrightconfig.json",
            ".coveragerc",
            "pytest.ini",
            ".importlinter",
            ".jscpd.json",
            "tox.ini",
        ),  # fmt: skip
        container_files=("pyproject.toml", "setup.cfg"),
    ),
    "typescript": LanguageRules(
        source_extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue"),
        test_file_patterns=(
            "*.test.ts",
            "*.test.tsx",
            "*.test.js",
            "*.test.jsx",
            "*.spec.ts",
            "*.spec.tsx",
            "*.spec.js",
            "*.spec.jsx",
            "*.test.mts",
            "*.spec.mts",
            "__tests__/*",
            "*/__tests__/*",
        ),  # fmt: skip
        skip_patterns={
            "it/test/describe.skip": (
                r"\b(it|test|describe|suite|context)(\.concurrent|\.sequential)?\."
                r"(skip|skipIf|todo|only)\b"
            ),
            "xit/xtest/xdescribe": r"\b(xit|xtest|xdescribe|fit|fdescribe)\s*\(",
            # В отчёте Vitest тест `it.fails` выглядит как «прошёл»: считаем его пропуском сами.
            "it.fails/test.fails": r"\b(it|test)(\.concurrent|\.sequential)?\.(fails|failing)\b",
        },
        suppression_patterns={
            "ts-ignore/expect-error/nocheck": r"@ts-(ignore|expect-error|nocheck)\b",
            "eslint-disable": r"\beslint-disable",
            "biome-ignore": r"\bbiome-ignore\b",
            "prettier-ignore": r"\bprettier-ignore\b",
            "coverage ignore": r"\b(istanbul|c8|v8)\s+ignore\b",
            "knip-ignore": r"@public\b|\bknip-ignore\b",
            # `any` главный способ «заглушить» типы: явные any считаются как подавления.
            "explicit any": (
                r":\s*any\b|\bas\s+any\b|<\s*any\s*>|[<,]\s*any\s*[,>\[]|\bany\s*\[\s*\]"
                r"|=>\s*any\b|\bsatisfies\s+any\b"
            ),
        },
        code_kinds=("explicit any",),
        config_files=(
            "tsconfig*.json",
            ".eslintrc*",
            "eslint.config.*",
            "biome.json",
            "biome.jsonc",
            ".prettierrc*",
            "prettier.config.*",
            "vitest.config.*",
            "vitest.workspace.*",
            "vite.config.*",
            "jest.config.*",
            ".dependency-cruiser.*",
            "dependency-cruiser.*",
            "knip.json",
            "knip.jsonc",
            ".knip.*",
            ".jscpd.json",
            ".nycrc*",
            ".c8rc*",
        ),  # fmt: skip
        container_files=("package.json",),
    ),
    "csharp": LanguageRules(
        source_extensions=(".cs",),
        test_file_patterns=("*Tests.cs", "*Test.cs", "*.Tests/*", "*/*.Tests/*", "*Tests/*"),
        skip_patterns={
            "Skip =": r"\bSkip\s*=",
            "[Ignore]/[Explicit]": r"\[\s*(Ignore|Explicit)\b",
            "Assert.Ignore": r"\bAssert\.(Ignore|Inconclusive)\s*\(",
            "Skip.If": r"\bSkip\.(If|IfNot)\s*\(",
        },
        suppression_patterns={
            "#pragma warning disable": r"#pragma\s+warning\s+disable",
            "SuppressMessage": r"\bSuppressMessage(Attribute)?\s*\(",
            "ReSharper disable": r"//\s*(ReSharper|noinspection)\b",
            "#nullable disable": r"#nullable\s+disable",
            "ExcludeFromCodeCoverage": r"\bExcludeFromCodeCoverage\b",
        },
        config_files=(
            ".editorconfig",
            "Directory.Build.props",
            "Directory.Build.targets",
            "Directory.Packages.props",
            "*.ruleset",
            "*.globalconfig",
            "*.runsettings",
            "global.json",
            "stylecop.json",
            ".jscpd.json",
            "coverlet.runsettings",
        ),  # fmt: skip
        container_files=("*.csproj",),
    ),
    "powershell": LanguageRules(
        source_extensions=(".ps1", ".psm1", ".psd1"),
        test_file_patterns=("*.Tests.ps1",),
        skip_patterns={
            "-Skip": r"\s-Skip\b",
            "Set-ItResult": r"\bSet-ItResult\b.*-(Skipped|Pending|Inconclusive)\b",
            "-Pending": r"\s-Pending\b",
        },
        suppression_patterns={
            "SuppressMessage": r"\bSuppressMessage(Attribute)?\s*\(",
            "PSScriptAnalyzer disable": r"#\s*(PSScriptAnalyzer|noqa)\b",
        },
        config_files=(
            "PSScriptAnalyzerSettings.psd1",
            "PesterConfiguration*",
            "pester.config.*",
            ".jscpd.json",
        ),  # fmt: skip
        container_files=(),
    ),
}

WALK_SKIP_DIRS = {
    ".git", ".github", ".claude", ".venv", "venv", "env", "node_modules", "__pycache__",
    "build", "dist", "bin", "obj", ".tox", "site-packages", ".mypy_cache", ".ruff_cache",
    ".pytest_cache",
}  # fmt: skip
PYPROJECT_SECTIONS = (
    "tool.ruff", "tool.pyright", "tool.pytest", "tool.coverage", "tool.importlinter",
    "tool.vulture", "tool.deptry", "tool.parch",
)  # fmt: skip
SETUP_CFG_PREFIXES = (
    "tool:pytest",
    "coverage:",
    "flake8",
    "pyright",
    "importlinter",
    "isort",
    "mypy",
)
PACKAGE_JSON_KEYS = (
    "jest", "vitest", "eslintConfig", "prettier", "c8", "nyc", "mocha", "ava", "parch", "knip",
)  # fmt: skip
PACKAGE_JSON_SCRIPTS = re.compile(
    r"^(test|lint|typecheck|type-check|check|coverage|format|arch|deadcode|knip)"
)
CSPROJ_ELEMENTS = {
    "Nullable", "TreatWarningsAsErrors", "WarningsAsErrors", "WarningsNotAsErrors", "NoWarn",
    "AnalysisMode", "AnalysisLevel", "EnforceCodeStyleInBuild", "LangVersion", "IsTestProject",
    "CollectCoverage", "Threshold", "ThresholdType",
}  # fmt: skip


def all_config_patterns() -> list[str]:
    return [p for rules in RULES.values() for p in rules.config_files]


def matches_any(name: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def is_pure_config(rel: str) -> bool:
    """Файл целиком состоит из настроек проверок (любой язык)."""
    return matches_any(PurePosixPath(rel).name, all_config_patterns())


def is_container(rel: str) -> bool:
    """Файл, в котором настройки проверок лежат лишь в части разделов (любой язык)."""
    name = PurePosixPath(rel).name
    patterns = [p for rules in RULES.values() for p in rules.container_files]
    return matches_any(name, patterns) or name.lower() == "directory.build.props"


def digest(text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def digest_value(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def container_settings(rel: str, text: str) -> dict[str, str]:
    """Отпечатки разделов с настройками проверок в pyproject.toml, setup.cfg, package.json, csproj.

    Разбор, который не удался, считается изменением (в отпечаток попадает хеш текста целиком):
    сломанный файл не должен «молча» оставлять настройки прежними.
    """
    name = PurePosixPath(rel).name.lower()
    found: dict[str, str] = {}
    try:
        if name == "pyproject.toml":
            data = as_dict(tomllib.loads(text))
            for section in PYPROJECT_SECTIONS:
                node: object = data
                for part in section.split("."):
                    node = as_dict(node).get(part)
                if node is not None:
                    found[f"{rel}#{section}"] = digest_value(node)
        elif name == "setup.cfg":
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
            for section in parser.sections():
                if section.startswith(SETUP_CFG_PREFIXES):
                    found[f"{rel}#{section}"] = digest_value(dict(parser.items(section)))
        elif name == "package.json":
            data = as_dict(json.loads(text))
            for key in PACKAGE_JSON_KEYS:
                if key in data:
                    found[f"{rel}#{key}"] = digest_value(data[key])
            for script, command in as_dict(data.get("scripts")).items():
                if PACKAGE_JSON_SCRIPTS.match(script):
                    found[f"{rel}#scripts.{script}"] = digest_value(command)
        elif name.endswith((".csproj", ".props")):
            values: dict[str, list[str]] = {}
            for element in ElementTree.fromstring(text).iter():
                tag = element.tag.split("}")[-1]
                if tag in CSPROJ_ELEMENTS:
                    values.setdefault(tag, []).append((element.text or "").strip())
            for tag, items in values.items():
                found[f"{rel}#{tag}"] = digest_value(items)
    except (ValueError, ElementTree.ParseError, configparser.Error):
        found[f"{rel}#НЕ-РАЗОБРАН"] = digest(text)
    return found


def settings_of_text(rel: str, text: str) -> dict[str, str]:
    if is_pure_config(rel):
        return {rel: digest(text)}
    if is_container(rel):
        return container_settings(rel, text)
    return {}


def walk_files(project: Path, extensions: tuple[str, ...] | None = None) -> list[Path]:
    found: list[Path] = []
    for folder, dirs, files in os.walk(project):
        dirs[:] = sorted(d for d in dirs if d not in WALK_SKIP_DIRS)
        for file in sorted(files):
            if extensions is None or file.lower().endswith(extensions):
                found.append(Path(folder) / file)
    return found


def rel_of(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def settings_fingerprint(project: Path, language: str) -> dict[str, str]:
    rules = RULES[language]
    current: dict[str, str] = {}
    for path in walk_files(project):
        rel = rel_of(project, path)
        name = PurePosixPath(rel).name
        if matches_any(name, rules.config_files) or matches_any(name, rules.container_files):
            current.update(
                settings_of_text(rel, path.read_text(encoding="utf-8", errors="replace"))
            )
    return current


def check_settings(project: Path, language: str) -> Result:
    result = Result()
    current = settings_fingerprint(project, language)
    baseline = Baseline(project)
    if not baseline.has("config", language):
        result.note(
            "Внимание: в baseline нет отпечатка настроек проверок, их изменение не будет замечено. "
            "Создайте его: baseline --update."
        )
        return result
    known = as_dict(baseline.section("config", language))
    changed = sorted(k for k in current if known.get(k) != current[k])
    removed = sorted(k for k in known if k not in current)
    if changed or removed:
        result.fail(
            "Настройки проверок изменены (ruff, pyright, pytest, coverage, import-linter, "
            "tsconfig, eslint, .editorconfig и аналоги):",
            *shown(
                [f"{k}: изменён или добавлен" for k in changed] + [f"{k}: удалён" for k in removed]
            ),
            "Ослабить проверки, правя их настройки, нельзя. Если изменение настоящее, его "
            "утверждает "
            "владелец: python .github/parch/parch_ci.py baseline --update --accept-config",
        )
    else:
        result.note(f"Настройки проверок не менялись (отпечатков: {len(current)}).")
    return result


# ---------- пропуски тестов и подавляющие комментарии ----------


def blank_python_comments_and_strings(text: str) -> tuple[str, list[str]]:
    """Код без комментариев и строк, а также тексты настоящих комментариев (через tokenize)."""
    lines = text.split("\n")
    comments: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                comments.append(token.string)
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                (start_row, start_col), (end_row, end_col) = token.start, token.end
                for row in range(start_row, end_row + 1):
                    line = lines[row - 1]
                    left = start_col if row == start_row else 0
                    right = end_col if row == end_row else len(line)
                    lines[row - 1] = line[:left] + " " * (right - left) + line[right:]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text, re.findall(r"#.*", text)
    return "\n".join(lines), comments


def blank_ts_comments_and_strings(text: str) -> tuple[str, str]:
    """Код без комментариев и содержимого строк, а также склеенный текст комментариев.

    Небольшой разбор вместо регулярных выражений: слово `it.skip` или `any` в строке или в
    комментарии пропуском и `any` не считается; подавления (`@ts-ignore`) ищутся в комментариях.
    Литералы регулярных выражений не разбираются (редкое ложное срабатывание допустимо).
    """
    out: list[str] = []
    comments: list[str] = []
    blank = re.compile(r"[^\n]")
    i, n = 0, len(text)
    while i < n:
        char, following = text[i], text[i + 1] if i + 1 < n else ""
        if char == "/" and following == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            comments.append(text[i:end])
            out.append(" " * (end - i))
            i = end
        elif char == "/" and following == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            comments.append(text[i:end])
            out.append(blank.sub(" ", text[i:end]))
            i = end
        elif char in "'\"`":
            j = i + 1
            while j < n and text[j] != char:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and char != "`":
                    break
                j += 1
            end = min(j + 1, n)
            closed = end - i > 1 and text[end - 1] == char
            body = text[i + 1 : end - 1] if closed else text[i + 1 : end]
            out.append(char + blank.sub(" ", body) + (char if closed else ""))
            i = end
        else:
            out.append(char)
            i += 1
    return "".join(out), "\n".join(comments)


def is_test_file(rules: LanguageRules, rel: str) -> bool:
    return matches_any(PurePosixPath(rel).name, rules.test_file_patterns) or any(
        fnmatch.fnmatch(rel.lower(), p.lower()) for p in rules.test_file_patterns if "/" in p
    )


def scan_counts(
    project: Path,
    language: str,
    patterns: dict[str, str],
    only_tests: bool,
    in_comments: bool = False,
) -> dict[str, int]:
    """Сколько раз встречается каждый вид (по файлам): «файл|вид» -> число.

    Для Python разбор через tokenize: подавления ищутся только в настоящих комментариях, пропуски
    только в коде (не в строках и не в комментариях). Для остальных языков поиск по тексту.
    """
    rules = RULES[language]
    counts: dict[str, int] = {}
    for path in walk_files(project, rules.source_extensions):
        rel = rel_of(project, path)
        if only_tests and not is_test_file(rules, rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        code_scope, comment_scope = text, text
        if language == "python":
            code_scope, comments = blank_python_comments_and_strings(text)
            comment_scope = "\n".join(comments)
        elif language == "typescript":
            code_scope, comment_scope = blank_ts_comments_and_strings(text)
        flags = re.MULTILINE | (re.IGNORECASE if language in ("python", "powershell") else 0)
        for kind, pattern in patterns.items():
            comment_kind = in_comments and kind not in rules.code_kinds
            scope = comment_scope if comment_kind else code_scope
            found = len(re.findall(pattern, scope, flags=flags))
            if found:
                counts[f"{rel}|{kind}"] = counts.get(f"{rel}|{kind}", 0) + found
    return counts


def count_dict(value: object) -> dict[str, int]:
    return {k: int(v) for k, v in as_dict(value).items() if isinstance(v, (int, float))}


def grown(current: dict[str, int], known: dict[str, int]) -> list[str]:
    return sorted(
        f"{k}: было {known.get(k, 0)}, стало {v}" for k, v in current.items() if v > known.get(k, 0)
    )


def check_counts(
    project: Path, language: str, section: str, title: str, hint: str, only_tests: bool
) -> Result:
    result = Result()
    rules = RULES[language]
    patterns = rules.skip_patterns if section == "skips" else rules.suppression_patterns
    current = scan_counts(
        project, language, patterns, only_tests, in_comments=section == "suppressions"
    )
    baseline = Baseline(project)
    known = count_dict(baseline.section(section, language))
    more = grown(current, known)
    if more:
        flag = "--accept-skips" if section == "skips" else "--accept-suppressions"
        result.fail(
            f"{title} (новых или больше, чем в baseline):",
            *shown(more),
            hint,
            "Если это решение владельца: "
            f"python .github/parch/parch_ci.py baseline --update {flag}",
        )
        return result
    result.note(
        f"{title}: не выросло (сейчас {sum(current.values())}, в baseline {sum(known.values())})."
    )
    if sum(current.values()) < sum(known.values()):
        result.note("Улучшение: стало меньше, чем в baseline. Закрепите: baseline --update.")
    if not baseline.has(section, language):
        result.note(
            "Внимание: в baseline нет этого раздела, рост не будет замечен. "
            "Создайте: baseline --update."
        )
    return result


# ---------- пропуски по фактическому результату запуска тестов ----------

SKIPPED_TRX = {"notexecuted", "inconclusive", "pending", "notrunnable"}
FAILED_TRX = {"failed", "error", "timeout", "aborted"}
SKIPPED_NUNIT = {"ignored", "skipped", "inconclusive", "notrun", "notrunnable", "pending"}
FAILED_NUNIT = {"failure", "error"}
SKIPPED_JEST = {"pending", "todo", "skipped", "disabled"}


def local_name(tag: str) -> str:
    return tag.split("}")[-1]


def junit_outcomes(path: Path) -> dict[str, str]:
    """JUnit XML (pytest --junitxml, Pester JUnitXml): исход каждого теста."""
    outcomes: dict[str, str] = {}
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "")
        key = f"{classname}::{name}" if classname else name
        children = {local_name(child.tag) for child in case}
        state = "skipped" if "skipped" in children else "passed"
        if children & {"failure", "error"}:
            state = "failed"
        outcomes[key] = state
    return outcomes


def nunit_outcomes(path: Path) -> dict[str, str]:
    """NUnit 2.5 XML (Pester NUnitXml): исход каждого теста."""
    outcomes: dict[str, str] = {}
    for case in ElementTree.parse(path).getroot().iter("test-case"):
        result = case.get("result", "").lower()
        state = "passed"
        if result in FAILED_NUNIT:
            state = "failed"
        elif result in SKIPPED_NUNIT or case.get("executed", "True").lower() == "false":
            state = "skipped"
        outcomes[case.get("name", "")] = state
    return outcomes


def pester_outcomes(path: Path) -> dict[str, str]:
    root = ElementTree.parse(path).getroot()
    return nunit_outcomes(path) if local_name(root.tag) == "test-results" else junit_outcomes(path)


def trx_outcomes(path: Path) -> dict[str, str]:
    """TRX (dotnet test --logger trx): исход каждого теста."""
    outcomes: dict[str, str] = {}
    for result in ElementTree.parse(path).getroot().iter():
        if local_name(result.tag) != "UnitTestResult":
            continue
        outcome = result.get("outcome", "").lower()
        state = "passed"
        if outcome in FAILED_TRX:
            state = "failed"
        elif outcome in SKIPPED_TRX:
            state = "skipped"
        outcomes[result.get("testName", "")] = state
    return outcomes


def jest_json_outcomes(path: Path) -> dict[str, str]:
    """JSON-отчёт Jest и Vitest (--json, --reporter=json): исход каждого теста."""
    outcomes: dict[str, str] = {}
    root = as_dict(json.loads(path.read_text(encoding="utf-8")))
    for file_result in as_list(root.get("testResults")):
        entry = as_dict(file_result)
        file_name = PurePosixPath(str(entry.get("name", "")).replace("\\", "/")).name
        for item in as_list(entry.get("assertionResults")):
            test = as_dict(item)
            status = str(test.get("status", ""))
            state = "failed" if status == "failed" else "passed"
            if status in SKIPPED_JEST:
                state = "skipped"
            outcomes[f"{file_name}::{test.get('fullName', test.get('title', ''))}"] = state
    return outcomes


REPORT_PARSERS: dict[str, Callable[[Path], dict[str, str]]] = {
    "python": junit_outcomes,
    "powershell": pester_outcomes,
    "typescript": jest_json_outcomes,
    "csharp": trx_outcomes,
}


def py_run_outcomes(project: Path) -> dict[str, str]:
    """Запускает тесты и возвращает исход каждого из отчёта JUnit XML."""
    report = project / ".parch-junit.xml"
    report.unlink(missing_ok=True)
    argv = [*python_tool("pytest"), "-q", "-p", "no:cacheprovider", "-o", "junit_family=xunit2"]
    done = run([*argv, f"--junitxml={report.name}"], project)
    try:
        if not report.is_file():
            raise ToolError("pytest не создал отчёт о запуске:\n" + tail(done))
        return junit_outcomes(report)
    finally:
        report.unlink(missing_ok=True)


def test_outcomes(project: Path, language: str, report: Path | None) -> dict[str, str] | None:
    """Исходы тестов из отчёта запуска; None, если для языка отчёт нужен, а его не передали."""
    if report is not None:
        return REPORT_PARSERS[language](report)
    if language == "python":
        return py_run_outcomes(project)
    return None


def skipped_ids(outcomes: dict[str, str]) -> set[str]:
    return {key for key, state in outcomes.items() if state == "skipped"}


REPORT_HINT = {
    "typescript": (
        "Jest: jest --json --outputFile=отчёт.json; "
        "Vitest: vitest run --reporter=json --outputFile=отчёт.json"
    ),
    "csharp": 'dotnet test --logger "trx;LogFileName=отчёт.trx"',
    "powershell": "Pester: Invoke-Pester -Output None -CI (отчёт NUnitXml или JUnitXml)",
}


def check_skips(project: Path, language: str, report: Path | None = None) -> Result:
    """Пропуски считаются по фактическому результату запуска, текстовый поиск идёт дополнительно."""
    result = check_counts(
        project,
        language,
        "skips",
        "Пропуски в тексте тестов (skip, skipif, xfail, only, Ignore, -Skip)",
        "Пропущенный тест не проверяет ничего: он считается удалённым. Почините тест или "
        "удалите его с решением владельца.",
        only_tests=True,
    )
    outcomes = test_outcomes(project, language, report)
    if outcomes is None:
        result.fail(
            f"Нет отчёта о запуске тестов для {language}: без него пропуски по фактическому "
            "результату не посчитать (одного текстового поиска мало: псевдонимы и динамические "
            "пропуски он не видит). Запустите тесты с отчётом и передайте --report ФАЙЛ.",
            REPORT_HINT.get(language, ""),
        )
        return result
    current = skipped_ids(outcomes)
    baseline = Baseline(project)
    known = baseline.strings("skipped_tests", language)
    new = sorted(current - known)
    if new:
        result.fail(
            f"Пропущено по фактическому результату запуска тестов ({len(new)} новых, в baseline "
            "их нет; сюда входят skip, skipif, xfail, todo, NotExecuted, Ignored):",
            *shown(new),
            "Это видно по отчёту запуска, поэтому псевдонимы (`import pytest as pt`, "
            "`from pytest import mark`) и пропуски внутри теста не обойти.",
            "Если это решение владельца: python .github/parch/parch_ci.py baseline --update "
            "--accept-skips",
        )
    else:
        result.note(
            f"Пропущенных тестов по результату запуска: {len(current)} (все известны из baseline)."
        )
        if not baseline.has("skipped_tests", language):
            result.note(
                "Внимание: в baseline нет списка пропущенных тестов. "
                "Создайте его: baseline --update."
            )
    return result


def check_suppressions(project: Path, language: str) -> Result:
    return check_counts(
        project,
        language,
        "suppressions",
        "Подавляющие комментарии (type: ignore, noqa, pragma: no cover, ts-ignore, pragma warning "
        "disable и аналоги)",
        "Не заглушайте проверку, исправьте причину. Число подавлений не должно расти.",
        only_tests=False,
    )


# ---------- TypeScript ----------

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
TS_COVERAGE_SUMMARY = "coverage/coverage-summary.json"
KNIP_CONFIGS = ("knip.json", "knip.jsonc", ".knip.json", ".knip.jsonc")
DEPCRUISE_CONFIGS = (
    ".dependency-cruiser.json",
    ".dependency-cruiser.cjs",
    ".dependency-cruiser.js",
    ".dependency-cruiser.mjs",
)
DEPCRUISE_EXAMPLE = (
    "Пример .dependency-cruiser.json в корне проекта:\n"
    '  {"forbidden": [{"name": "ui-not-db", "severity": "error",\n'
    '     "from": {"path": "^src/ui/"}, "to": {"path": "^src/db/"}}],\n'
    '   "options": {"doNotFollow": {"path": "node_modules"},'
    ' "tsConfig": {"fileName": "tsconfig.json"}}}'
)


def node_exe() -> str:
    return shutil.which("node") or "node"


def node_bin(project: Path, package: str, binary: str | None = None) -> list[str]:
    """Запуск пакета из node_modules напрямую через node, без обёрток .cmd (одинаково везде)."""
    manifest = project / "node_modules" / package / "package.json"
    if not manifest.is_file():
        raise ToolError(f"пакет {package} не установлен: выполните npm ci")
    bins = read_json(manifest).get("bin")
    mapping = {package.split("/")[-1]: str(bins)} if isinstance(bins, str) else as_dict(bins)
    entry = mapping.get(binary or package.split("/")[-1]) or next(iter(mapping.values()), None)
    if not entry:
        raise ToolError(f"у пакета {package} нет исполняемого файла")
    return [node_exe(), str((manifest.parent / str(entry)).resolve())]


def ts_source_roots(project: Path) -> list[str]:
    parch = as_dict(read_json(project / "package.json").get("parch"))
    configured = as_strings(parch.get("sourceRoots"))
    if configured:
        return [normalize_path(x) for x in configured]
    return ["src"] if (project / "src").is_dir() else ["."]


def ts_is_source(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith(TS_EXTENSIONS) or lower.endswith(".d.ts"):
        return False
    return not any(marker in lower for marker in (".test.", ".spec.", ".config."))


def ts_modules(project: Path) -> list[str]:
    """Модули: папки и одиночные файлы кода прямо в корне кода (пути от корня проекта)."""
    modules: list[str] = []
    for root in ts_source_roots(project):
        base = project / root
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            rel = normalize_path(entry.relative_to(project).as_posix())
            if entry.is_dir():
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue
                if any(ts_is_source(f.name) for f in entry.rglob("*") if f.is_file()):
                    modules.append(rel)
            elif ts_is_source(entry.name):
                modules.append(rel)
    return modules


def ts_module_names(project: Path) -> list[str]:
    return [PurePosixPath(m).stem if PurePosixPath(m).suffix else PurePosixPath(m).name
            for m in ts_modules(project)]  # fmt: skip


def ts_has_sources(project: Path) -> bool:
    return bool(ts_modules(project))


def ts_test_ids(project: Path, report: Path | None) -> list[str]:
    if report is None:
        raise ToolError(
            "для TypeScript нужен отчёт о запуске тестов (--report ФАЙЛ): "
            "список тестов берётся из него. " + REPORT_HINT["typescript"]
        )
    return sorted(jest_json_outcomes(report))


NON_REGISTRY_SPEC = re.compile(r"^(git\+|git:|https?:|file:|github:|link:|[\w.-]+/[\w.-]+$)")


def ts_manifest_packages(project: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in walk_files(project, ("package.json",)):
        rel = rel_of(project, path)
        data = read_json(path)
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            for name, spec in as_dict(data.get(section)).items():
                found.append((rel, name))
                if isinstance(spec, str) and NON_REGISTRY_SPEC.match(spec):
                    found.append(
                        (rel, f"{name}@{spec}")
                    )  # источник вне реестра: отдельное нарушение
    return found


def knip_config_file(project: Path) -> tuple[str, dict[str, object]] | None:
    for name in KNIP_CONFIGS:
        path = project / name
        if path.is_file():
            text = re.sub(
                r"/\*.*?\*/|^\s*//.*$", "", path.read_text(encoding="utf-8"), flags=re.S | re.M
            )
            try:
                return name, as_dict(json.loads(text))
            except ValueError:
                return name, {"__unparseable__": True}
    package = as_dict(read_json(project / "package.json").get("knip"))
    return ("package.json", package) if package else None


def is_broad_pattern(pattern: str) -> bool:
    """Исключение, которое закрывает почти весь код: `**`, `src/**`, `**/*.ts`, `*`."""
    text = pattern.strip().removeprefix("./").removeprefix("!")
    if not any(ch in text for ch in "*?["):
        return False
    literal: list[str] = []
    for segment in text.split("/"):
        if any(ch in segment for ch in "*?["):
            break
        literal.append(segment)
    return len(literal) <= 1


def knip_ignore_patterns(config: dict[str, object]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for key, value in config.items():
        if key == "workspaces":
            for sub in as_dict(value).values():
                found += knip_ignore_patterns(as_dict(sub))
        elif key.startswith("ignore"):
            if as_dict(value):
                found += [(key, str(pattern)) for pattern in as_dict(value)]
            else:
                found += [(key, pattern) for pattern in as_strings(value)]
    return found


def knip_broad_findings(project: Path) -> set[str]:
    loaded = knip_config_file(project)
    if loaded is None:
        return set()
    name, config = loaded
    if config.get("__unparseable__"):
        return {f"knip-config|{name}|unparseable|"}
    return {
        f"knip-config|{name}|{key}|{pattern}"
        for key, pattern in knip_ignore_patterns(config)
        if is_broad_pattern(pattern)
    }


def knip_findings(project: Path) -> set[str]:
    done = run([*node_bin(project, "knip"), "--reporter", "json"], project)
    if done.returncode not in (0, 1):
        raise ToolError("knip не отработал:\n" + tail(done))
    try:
        data = as_dict(json.loads(done.stdout))
    except ValueError as error:
        raise ToolError("knip вернул не JSON:\n" + tail(done)) from error
    found: set[str] = set()
    for item in as_list(data.get("issues")):
        entry = as_dict(item)
        file = normalize_path(str(entry.get("file", "")))
        for category, values in entry.items():
            if category == "file":
                continue
            for value in as_list(values):
                found.add(f"knip|{file}|{category}|{as_dict(value).get('name', '')}")
    return found


def ts_dead_code(project: Path) -> set[str]:
    broad = knip_broad_findings(project)
    try:
        return knip_findings(project) | broad
    except ToolError:
        if broad:  # knip мог упасть как раз из-за такой настройки: показываем причину
            return broad
        raise


def ts_coverage(project: Path) -> float:
    path = project / TS_COVERAGE_SUMMARY
    if not path.is_file():
        raise ToolError(
            f"нет {TS_COVERAGE_SUMMARY}: тесты нужно запускать с покрытием "
            "(vitest run --coverage, отчёт json-summary)"
        )
    total = as_dict(as_dict(read_json(path).get("total")).get("lines"))
    pct = total.get("pct")
    if not isinstance(pct, (int, float)):
        raise ToolError(f"в {TS_COVERAGE_SUMMARY} нет total.lines.pct")
    return float(pct)


def depcruise_config(project: Path) -> tuple[Path, dict[str, object]] | None:
    for name in DEPCRUISE_CONFIGS:
        path = project / name
        if not path.is_file():
            continue
        if path.suffix == ".json":
            return path, read_json(path)
        loader = (
            "import(require('url').pathToFileURL(process.argv[1]).href)"
            ".then(m => console.log(JSON.stringify(m.default ?? m)))"
        )
        done = run([node_exe(), "-e", loader, str(path)], project)
        if done.returncode != 0:
            raise ToolError(f"не удалось прочитать {name}:\n" + tail(done))
        return path, as_dict(json.loads(done.stdout))
    return None


def rule_patterns(rule: dict[str, object]) -> list[tuple[str, str]]:
    """Регулярные выражения путей из правила: (откуда `from` или куда `to`, шаблон)."""
    found: list[tuple[str, str]] = []
    for side in ("from", "to"):
        node = as_dict(rule.get(side))
        value = node.get("path")
        patterns = as_strings(value) if as_list(value) else ([str(value)] if value else [])
        found += [(side, pattern) for pattern in patterns]
    return found


def ts_architecture(project: Path) -> Result:
    result = Result()
    names = ts_module_names(project)
    if not names:
        result.note("Кода пока нет, проверка архитектуры пропущена.")
        return result
    loaded = depcruise_config(project)
    if loaded is None:
        result.fail(
            "Для модулей кода нет правил архитектуры (.dependency-cruiser.json).", DEPCRUISE_EXAMPLE
        )
        return result
    config_path, config = loaded
    rules = [as_dict(r) for key in ("forbidden", "allowed") for r in as_list(config.get(key))]
    path_rules = [r for r in rules if rule_patterns(r)]
    if not path_rules:
        result.fail(
            "В правилах dependency-cruiser нет ни одного правила с путями (from.path / to.path): "
            "правило, которого нет, ничего не проверяет.",
            DEPCRUISE_EXAMPLE,
        )
        return result
    roots = [r for r in ts_source_roots(project) if (project / r).exists()]
    argv = [*node_bin(project, "dependency-cruiser", "depcruise"), *roots]
    argv += ["--config", config_path.name, "--output-type", "json"]
    done = run(argv, project)
    try:
        report = as_dict(json.loads(done.stdout))
    except ValueError as error:
        raise ToolError("dependency-cruiser вернул не JSON:\n" + tail(done)) from error
    modules = as_list(report.get("modules"))
    if not modules:
        result.fail(
            "dependency-cruiser не проанализировал ни одного файла: правила «пусто-зелёные». "
            "Частая причина: несовместимая версия TypeScript или неверный путь к коду.",
            *tail(done, 600).splitlines()[-5:],
        )
        return result
    universe: set[str] = set()
    for module in modules:
        entry = as_dict(module)
        universe.add(str(entry.get("source", "")))
        universe.update(
            str(as_dict(d).get("resolved", "")) for d in as_list(entry.get("dependencies"))
        )
    empty: list[str] = []
    covered: set[str] = set()
    for rule in path_rules:
        for side, pattern in rule_patterns(rule):
            try:
                matched = {name for name in universe if re.search(pattern, name)}
            except re.error as error:
                empty.append(
                    f"{rule.get('name', '?')}: {side}.path «{pattern}» не разобран ({error})"
                )
                continue
            if not matched:
                empty.append(
                    f"{rule.get('name', '?')}: {side}.path «{pattern}» не находит ни одного файла"
                )
            covered |= matched
    if empty:
        result.fail(
            "Правила архитектуры ничего не охватывают (опечатка в пути?). "
            "Такое правило «проходит», "
            "ничего не проверяя, поэтому оно считается ошибкой:",
            *shown(empty),
        )
        return result
    uncovered = [m for m in ts_modules(project) if not any(c.startswith(m) for c in covered)]
    if uncovered:
        result.fail(
            "Эти модули не охвачены ни одним правилом архитектуры (нет в from.path и to.path):",
            *shown(uncovered),
        )
        return result
    summary = as_dict(report.get("summary"))
    violations = [as_dict(v) for v in as_list(summary.get("violations"))]
    errors = [v for v in violations if as_dict(v.get("rule")).get("severity") == "error"]
    if errors:
        lines = [
            f"{as_dict(v.get('rule')).get('name')}: {v.get('from')} -> {v.get('to')}"
            for v in errors
        ]
        result.fail("Правила архитектуры нарушены:", *shown(lines))
    else:
        result.note(f"Правила архитектуры соблюдены (правил с путями: {len(path_rules)}).")
    return result


# ---------- адаптеры языков ----------


@dataclass(frozen=True)
class Collector:
    ecosystem: str
    jscpd_format: str
    test_ids: Callable[[Path, Path | None], list[str]]
    modules: Callable[[Path], list[str]]
    module_names: Callable[[Path], list[str]]
    manifest_packages: Callable[[Path], list[tuple[str, str]]]
    dead_code: Callable[[Path], set[str]]
    coverage: Callable[[Path], float]
    has_sources: Callable[[Path], bool]
    architecture: Callable[[Path], Result]


COLLECTORS: dict[str, Collector] = {
    "python": Collector(
        "pip",
        "python",
        py_test_ids,
        py_modules,
        py_module_names,
        py_manifest_packages,
        py_dead_code,
        py_coverage,
        py_has_sources,
        py_architecture,
    ),
    "typescript": Collector(
        "npm",
        "typescript,tsx",
        ts_test_ids,
        ts_modules,
        ts_module_names,
        ts_manifest_packages,
        ts_dead_code,
        ts_coverage,
        ts_has_sources,
        ts_architecture,
    ),
}

CHECKS: dict[str, Callable[[Path, str], Result]] = {
    "tests": check_tests,
    "modules": check_modules,
    "deps": check_deps,
    "dead-code": check_dead_code,
    "duplicates": check_duplicates,
    "architecture": check_architecture,
    "coverage": check_coverage,
    "skips": check_skips,
    "suppressions": check_suppressions,
    "settings": check_settings,
}
COLLECTOR_CHECKS = {
    "tests",
    "modules",
    "deps",
    "dead-code",
    "duplicates",
    "architecture",
    "coverage",
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="parch_ci")
    parser.add_argument("check", choices=[*CHECKS, "baseline"])
    parser.add_argument("--language", default="python", choices=list(RULES))
    parser.add_argument("--project", default=".")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--accept-new", action="store_true")
    parser.add_argument("--accept-removed", action="store_true")
    parser.add_argument("--accept-skips", action="store_true")
    parser.add_argument("--accept-suppressions", action="store_true")
    parser.add_argument("--accept-config", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    report = Path(args.report).resolve() if args.report else None
    try:
        if args.check == "baseline":
            if not args.update:
                parser.error("для baseline нужен флаг --update")
            accept = Accept(
                new=args.accept_new,
                removed=args.accept_removed,
                skips=args.accept_skips,
                suppressions=args.accept_suppressions,
                config=args.accept_config,
            )
            result = update_baseline(project, args.language, accept, report)
        elif args.check in COLLECTOR_CHECKS and args.language not in COLLECTORS:
            print(
                f"[parch:{args.check}] ПРОВАЛ: для языка {args.language} эта проверка "
                "ещё не реализована"
            )
            return 1
        elif args.check == "tests":
            result = check_tests(project, args.language, report)
        elif args.check == "skips":
            result = check_skips(project, args.language, report)
        else:
            result = CHECKS[args.check](project, args.language)
    except ToolError as error:
        print(f"[parch:{args.check}] ПРОВАЛ: {error}")
        return 1
    except (ValueError, OSError, ElementTree.ParseError) as error:
        print(f"[parch:{args.check}] ПРОВАЛ: не удалось прочитать данные или настройки: {error}")
        return 1
    print(f"[parch:{args.check}] {'ok' if result.ok else 'ПРОВАЛ'}")
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
