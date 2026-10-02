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
  baseline      обновить baseline (--update); новое нарушение требует --accept-new,
                пропавший тест требует --accept-removed

«Храповик»: старые нарушения (они записаны в baseline) CI пропускает, любое новое останавливает.
Baseline лежит в state/baseline.json и state/jscpd-baseline.json; менять его может только владелец.
Код выхода: 0 всё хорошо; 1 нарушение или сломавшаяся проверка (молча «зелёным» не считается).
"""

from __future__ import annotations

import argparse
import configparser
import io
import json
import os
import re
import shlex
import subprocess
import sys
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
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "site-packages",
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
            env={**os.environ, **(env or {})},
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


def py_test_ids(project: Path) -> list[str]:
    argv = [*python_tool("pytest"), "--collect-only", "-q", "-p", "no:cacheprovider"]
    done = run(argv, project)
    if done.returncode not in (0, 5):
        raise ToolError("pytest не смог собрать тесты:\n" + tail(done))
    lines = (line.strip() for line in done.stdout.splitlines())
    return sorted({line for line in lines if "::" in line and " " not in line})


def check_tests(project: Path, language: str) -> Result:
    result = Result()
    current = set(COLLECTORS[language].test_ids(project))
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


def jscpd_args(project: Path, language: str) -> list[str]:
    roots = [r for r in py_source_roots(project) if (project / r).exists()]
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
    result = Result()
    collector = COLLECTORS[language]
    names = collector.module_names(project) if collector.has_sources(project) else []
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


def update_baseline(project: Path, language: str, accept_new: bool, accept_removed: bool) -> Result:
    result = Result()
    collector = COLLECTORS[language]
    baseline = Baseline(project)
    tests = set(collector.test_ids(project))
    removed = sorted(baseline.strings("tests", language) - tests)
    if removed and not accept_removed:
        result.fail(
            "Отказ: из baseline пропали бы тесты. Их удаление должен утвердить владелец "
            "(флаг --accept-removed):",
            *shown(removed),
        )
    has_sources = collector.has_sources(project)
    dead = collector.dead_code(project) if has_sources else set[str]()
    initialized = baseline.has("dead_code", language)
    new_dead = sorted(dead - baseline.strings("dead_code", language))
    if new_dead and initialized and not accept_new:
        result.fail(
            "Отказ: это новые нарушения, записывать их в baseline без решения владельца нельзя "
            "(флаг --accept-new):",
            *shown([describe_finding(f) for f in new_dead]),
        )
    if has_sources:
        ensure_empty_jscpd_baseline(project)
        probe = [*jscpd_command(), *jscpd_args(project, language), "--fail-on-new-clones", "0"]
        if run(probe, project).returncode != 0 and initialized and not accept_new:
            result.fail(
                "Отказ: в коде есть новые дубли, записывать их в baseline нельзя (--accept-new)."
            )
    if not result.ok:
        return result
    baseline.set("tests", language, sorted(tests))
    baseline.set("dead_code", language, sorted(dead))
    if has_sources:
        baseline.set("coverage", language, round(collector.coverage(project), 2))
        refresh = [*jscpd_command(), *jscpd_args(project, language), "--update-baseline"]
        updated = run(refresh, project)
        if updated.returncode != 0:
            raise ToolError("jscpd не смог обновить baseline:\n" + tail(updated, 800))
    baseline.save()
    result.note(
        f"baseline обновлён: тестов {len(tests)}, известных находок мёртвого кода {len(dead)}."
    )
    return result


# ---------- адаптеры языков ----------


@dataclass(frozen=True)
class Collector:
    ecosystem: str
    jscpd_format: str
    test_ids: Callable[[Path], list[str]]
    modules: Callable[[Path], list[str]]
    module_names: Callable[[Path], list[str]]
    manifest_packages: Callable[[Path], list[tuple[str, str]]]
    dead_code: Callable[[Path], set[str]]
    coverage: Callable[[Path], float]
    has_sources: Callable[[Path], bool]


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
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="parch_ci")
    parser.add_argument("check", choices=[*CHECKS, "baseline"])
    parser.add_argument("--language", default="python", choices=list(COLLECTORS))
    parser.add_argument("--project", default=".")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--accept-new", action="store_true")
    parser.add_argument("--accept-removed", action="store_true")
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    try:
        if args.check == "baseline":
            if not args.update:
                parser.error("для baseline нужен флаг --update")
            result = update_baseline(project, args.language, args.accept_new, args.accept_removed)
        else:
            result = CHECKS[args.check](project, args.language)
    except ToolError as error:
        print(f"[parch:{args.check}] ПРОВАЛ: {error}")
        return 1
    print(f"[parch:{args.check}] {'ok' if result.ok else 'ПРОВАЛ'}")
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
