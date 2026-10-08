"""Проверки CI ProjectArchitect (общие для всех языков).

Только стандартная библиотека. В проект копируется как .github/parch/parch_ci.py.
Запуск из корня проекта:  python .github/parch/parch_ci.py <проверка> [--language python]

Проверки:
  tests         число тестов не уменьшилось: каждый тест из baseline на месте
  libraries     низкоуровневая библиотека подключается только в одном модуле (правила в
                state/architecture.json; C#, TypeScript, PowerShell; код в parch_libraries.py)
  modules       каждый модуль кода есть в docs/MODULES.md
  catalog       у публичных функций есть однострочное описание, каталог не отстал от кода
                (--update пересобирает docs/CAPABILITIES.md; код в parch_catalog.py, Python и C#);
                старые функции без описания записаны в state/catalog-baseline.json, число только
                снижается; записать новый долг может владелец: catalog --update --accept-new
  deps          каждый пакет из манифестов есть в разделе «Разрешённые пакеты» CONSTITUTION.md
  dead-code     мёртвый код и лишние зависимости (vulture, deptry), «храповик» по baseline
  duplicates    дубли кода (jscpd), «храповик» по baseline
  architecture  правила архитектуры (import-linter); правило, которое ничего не охватывает, падает
  coverage      покрытие тестами не ниже baseline
  skips         пропущенные тесты (skip, xfail, Ignore, -Skip) считаются удалёнными
  suppressions  подавляющие комментарии (type: ignore, noqa, ts-ignore): рост запрещён
  settings      настройки проверок (ruff, pyright, tsconfig, eslint...) не менялись
  basis         «Основания» из коммитов ветки (git log main..HEAD): печатается для описания PR
  baseline      обновить baseline (--update); каждое ухудшение требует флага владельца:
                --accept-new, --accept-removed, --accept-skips,
                --accept-suppressions, --accept-config
                --only-tests: обновить только списки тестов и пропусков

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
import tempfile
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


PY_TEST_ID = re.compile(r"^[^\s:]+\.py::\S")


def py_test_ids(project: Path, report: Path | None = None) -> list[str]:
    """Тесты Python собираются самим pytest; отчёт не нужен."""
    del report
    # `-o addopts=`: настройки по умолчанию (параллельный запуск, фильтр медленных тестов) не должны
    # скрывать тесты от учёта: число тестов всегда считается по полному сбору.
    argv = [*python_tool("pytest"), "--collect-only", "-q", "-p", "no:cacheprovider"]
    argv += ["-o", "addopts="]
    done = run(argv, project)
    if done.returncode not in (0, 5):
        raise ToolError("pytest не смог собрать тесты:\n" + tail(done))
    lines = (line.strip() for line in done.stdout.splitlines())
    # идентификатор теста начинается с пути к файлу; в скобках параметров бывают пробелы
    return sorted({line for line in lines if PY_TEST_ID.match(line)})


# ---------- тесты, которых нет в отчёте запуска ----------

TS_TEST_CALL = re.compile(r"\b(it|test)(\.\w+)*\s*[(`]")
CS_TEST_ATTRIBUTE = re.compile(r"\[\s*(Fact|Theory|Test|TestCase|TestMethod|DataTestMethod)\b")
CS_CLASS = re.compile(r"\bclass\s+(\w+)")
GAP_HINT = (
    "Тест, который есть в коде или в baseline, но отсутствует в отчёте запуска, считается не "
    "запущенным: урезанный отчёт или фильтр (-k, --filter) ничего не доказывает. "
    "Запустите все тесты."
)


def py_report_key(test_id: str) -> str:
    """Идентификатор pytest `tests/a.py::Класс::имя[пар]` в виде ключа отчёта JUnit."""
    head, bracket, params = test_id.partition("[")
    parts = head.split("::")
    module = parts[0].removesuffix(".py").replace("\\", "/").replace("/", ".")
    classname = ".".join([module, *parts[1:-1]])
    return f"{classname}::{parts[-1]}{bracket}{params}"


def ts_files_without_results(project: Path, reported: set[str]) -> set[str]:
    rules = RULES["typescript"]
    in_report = {key.split("::")[0] for key in reported}
    gaps: set[str] = set()
    for path in walk_files(project, TS_EXTENSIONS):
        rel = rel_of(project, path)
        if not is_test_file(rules, rel):
            continue
        code, _ = blank_ts_comments_and_strings(path.read_text(encoding="utf-8", errors="replace"))
        if TS_TEST_CALL.search(code) and path.name not in in_report:
            gaps.add(f"{rel} (файл с тестами, в отчёте его нет)")
    return gaps


def cs_classes_without_results(project: Path, reported: set[str]) -> set[str]:
    rules = RULES["csharp"]
    in_report = {parts[-2] for key in reported if len(parts := key.split("(")[0].split(".")) >= 2}
    gaps: set[str] = set()
    for path in walk_files(project, (".cs",)):
        rel = rel_of(project, path)
        if not is_test_file(rules, rel):
            continue
        code, _ = blank_cs_comments_and_strings(path.read_text(encoding="utf-8-sig"))
        starts = [(m.start(), m.group(1)) for m in CS_CLASS.finditer(code)]
        for index, (start, name) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(code)
            if CS_TEST_ATTRIBUTE.search(code[start:end]) and name not in in_report:
                gaps.add(f"{rel}: класс {name} (тесты класса в отчёте не найдены)")
    return gaps


PARTIAL_SUITE = "parch-partial"
PARTIAL_HINT = (
    "Урезанный прогон помечается при запуске: pytest -o junit_suite_name=parch-partial. "
    "Полный прогон подтверждает слияние, урезанный нет."
)


def report_is_partial(report: Path | None) -> bool:
    """True, если отчёт помечен как урезанный (JUnit XML с именем набора parch-partial)."""
    if report is None or report.is_dir():
        return False
    text = report.read_text(encoding="utf-8-sig", errors="replace")
    if not text.lstrip().startswith("<"):
        return False
    root = ElementTree.fromstring(text)
    return any(
        el.get("name") == PARTIAL_SUITE for el in root.iter() if local_name(el.tag) == "testsuite"
    )


def partial_mismatch(report: Path | None, partial: bool) -> str:
    """Не даёт выдать урезанный отчёт за полный и наоборот: храповик различает режимы."""
    marked = report_is_partial(report)
    if marked and not partial:
        return (
            "Отчёт помечен как урезанный (parch-partial), а проверяется полный прогон: такой "
            "отчёт не подтверждает, что все тесты запущены и прошли. Нужен полный прогон. "
            + PARTIAL_HINT
        )
    if partial and report is not None and not marked:
        return "Режим --partial требует отчёт урезанного прогона с пометкой. " + PARTIAL_HINT
    return ""


def report_gaps(
    project: Path,
    language: str,
    outcomes: dict[str, str],
    baseline: Baseline,
    include_known: bool = True,
) -> list[str]:
    """Тесты из кода и из baseline, которых нет в отчёте запуска.

    include_known=False: тесты из baseline не берутся (владелец утвердил их удаление).
    """
    reported = set(outcomes)
    known = baseline.strings("tests", language) if include_known else set[str]()
    if language == "python":
        found = set(py_test_ids(project)) | known
        return sorted(t for t in found if py_report_key(t) not in reported)
    gaps = {t for t in known if t not in reported}
    if language == "typescript":
        gaps |= ts_files_without_results(project, reported)
    if language == "csharp":
        gaps |= cs_classes_without_results(project, reported)
    return sorted(gaps)


def fail_on_gaps(result: Result, gaps: list[str]) -> bool:
    if gaps:
        result.fail(
            f"В отчёте запуска нет {len(gaps)} тестов, которые есть в коде или в baseline:",
            *shown(gaps),
            GAP_HINT,
        )
    return bool(gaps)


def check_tests(
    project: Path, language: str, report: Path | None = None, partial: bool = False
) -> Result:
    result = Result()
    mismatch = partial_mismatch(report, partial)
    if mismatch:
        result.fail(mismatch)
        return result
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
    if report is not None and partial:
        outcomes = test_outcomes(project, language, report) or {}
        failed = sorted(k for k, v in outcomes.items() if v == "failed")
        if failed:
            result.fail(f"Упавших тестов в урезанном прогоне: {len(failed)}.", *shown(failed))
            return result
        result.note(
            f"ЧАСТИЧНЫЙ прогон: запущено {len(outcomes)} из {len(current)} тестов, упавших нет. "
            "Это не полный прогон: права на слияние он не даёт, baseline по нему не обновляется."
        )
    elif report is not None:
        outcomes = test_outcomes(project, language, report)
        if outcomes is not None and fail_on_gaps(
            result, report_gaps(project, language, outcomes, baseline)
        ):
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


MODULES_DEBT_FILE = "state/modules-baseline.json"
EMPTY_BLOCK_CELLS = {"", "?", "-", "—", "–"}  # так в таблице записано «блока нет»


@dataclass
class ModuleRow:
    path: str
    status: str
    blocks: list[str]


def modules_with_blocks(project: Path) -> tuple[bool, list[ModuleRow]]:
    """(есть ли колонка «Блок», строки таблицы MODULES.md): путь, статус, блоки модуля."""
    path = project / "docs" / "MODULES.md"
    if not path.is_file():
        raise ToolError("нет docs/MODULES.md: карта модулей обязательна")
    column: int | None = None
    status_column: int | None = None
    rows: list[ModuleRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if len(cells) >= 2 and lowered[1] in {"путь", "path"}:
            column = next((i for i, c in enumerate(lowered) if c in {"блок", "block"}), None)
            status_column = next(
                (i for i, c in enumerate(lowered) if c in {"статус", "status"}), None
            )
            continue
        if len(cells) < 2 or not cells[1] or set(cells[1]) <= set("-: "):
            continue
        status = (
            cells[status_column] if status_column is not None and status_column < len(cells) else ""
        )
        raw = cells[column] if column is not None and column < len(cells) else ""
        blocks = [b for b in re.split(r"[\s,;]+", raw) if b not in EMPTY_BLOCK_CELLS]
        rows.append(ModuleRow(normalize_path(cells[1]), status.lower(), blocks))
    return column is not None, rows


def feature_ids(project: Path) -> set[str] | None:
    path = project / "state" / "features.json"
    if not path.is_file():
        return None
    return {
        str(as_dict(item).get("id"))
        for item in as_list(read_json(path).get("features"))
        if as_dict(item).get("id")
    }


def read_modules_debt(project: Path) -> set[str] | None:
    path = project / MODULES_DEBT_FILE
    if not path.is_file():
        return None
    return set(as_strings(read_json(path).get("without_block")))


def write_modules_debt(project: Path, debt: set[str]) -> None:
    path = project / MODULES_DEBT_FILE
    if not debt:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": 1, "without_block": sorted(debt)}, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def check_module_blocks(
    project: Path, result: Result, update: bool = False, accept_new: bool = False
) -> None:
    """Связь «модуль → блок» (F18): блок существует; новый модуль без блока падает, старый нет."""
    has_column, rows = modules_with_blocks(project)
    if not has_column:
        # Колонка нужна всегда. Единственное послабление: проект записан как существующий и ещё
        # не перенесён (`existing_project` в baseline, его ставит init-project при подключении
        # проекта с готовым кодом) и файла долга по блокам нет. Удалив колонку, проверку
        # отключить нельзя: на новом проекте и на проекте с долгом это провал.
        existing = Baseline(project).data.get("existing_project") is True
        has_debt_file = read_modules_debt(project) is not None
        if existing and not has_debt_file:
            result.note(
                "В docs/MODULES.md нет колонки «Блок»: проект записан как существующий и ещё "
                "не перенесён, поэтому только предупреждение. Добавьте колонку (образец: шаблон "
                "MODULES.md продукта), впишите блок каждому модулю и запишите долг: "
                "modules --update --accept-new."
            )
            return
        reason = (
            "есть файл долга по блокам, значит перенос начался"
            if has_debt_file
            else "проект не записан как существующий, колонка обязательна"
        )
        result.fail(
            f"В docs/MODULES.md нет колонки «Блок» ({reason}): без неё модули не связаны с "
            "блоками цели. Верните колонку «Блок» в таблицу (образец: шаблон MODULES.md продукта)."
        )
        return
    known = feature_ids(project)
    if known is not None:
        unknown = sorted(
            f"{row.path}: блок {b}" for row in rows for b in row.blocks if b not in known
        )
        if unknown:
            result.fail(
                "В MODULES.md указан блок, которого нет в state/features.json "
                "(опечатка или блок удалён):",
                *shown(unknown),
            )
    current = {
        row.path for row in rows if not row.blocks and not row.status.startswith("keep-until")
    }
    debt = read_modules_debt(project)
    fresh = sorted(current if debt is None else current - debt)
    if fresh and not accept_new:
        result.fail(
            "У этих модулей нет блока (колонка «Блок» в docs/MODULES.md): код без цели "
            "кандидат на удаление. "
            "Впишите блок из state/features.json или статус keep-until:ДАТА по решению владельца:",
            *shown(fresh),
        )
        return
    if update:
        new_debt = current if accept_new else current & (debt or set())
        if new_debt != (debt or set()):
            write_modules_debt(project, new_debt)
            result.note(f"Долг по блокам записан: модулей без блока {len(new_debt)}.")
    elif debt is not None and debt - current:
        result.note(
            f"У {len(debt - current)} модулей из долга появился блок или они исчезли: "
            "сократите baseline командой modules --update."
        )
    if current:
        result.note(
            f"Модулей без блока: {len(current)} (в долге у владельца, число только снижается)."
        )


def check_modules(
    project: Path, language: str, update: bool = False, accept_new: bool = False
) -> Result:
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
    check_module_blocks(project, result, update, accept_new)
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
    parts = fingerprint.split("|", 3)
    if parts[0] == "roslyn":
        return f"{parts[2]}: {parts[1]} {parts[3]}"
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
    # на Windows npx это npx.cmd: subprocess без полного пути его не находит
    npx = shutil.which("npx") or "npx"
    return shlex.split(override) if override else [npx, "--yes", f"jscpd@{JSCPD_VERSION}"]


def source_roots(project: Path, language: str) -> list[str]:
    """Корни кода проекта для языка (для остальных языков пока весь проект)."""
    if language == "python":
        return py_source_roots(project)
    if language == "typescript":
        return ts_source_roots(project)
    if language == "csharp":
        return cs_source_roots(project)
    return ["."]


def jscpd_args(
    project: Path,
    language: str,
    min_lines: str = JSCPD_MIN_LINES,
    min_tokens: str = JSCPD_MIN_TOKENS,
) -> list[str]:
    roots = [r for r in source_roots(project, language) if (project / r).exists()]
    ignored = [
        "tests", ".github", "docs", "state", "node_modules", ".venv", "__pycache__", "bin", "obj",
        CS_REPORT_DIR,
    ]  # fmt: skip
    ignore = ",".join(f"**/{name}/**" for name in ignored)
    return [
        *roots,
        *("--format", COLLECTORS[language].jscpd_format),
        *("--min-lines", min_lines),
        *("--min-tokens", min_tokens),
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


def only_short_files(project: Path, language: str) -> bool:
    """jscpd «не проанализировал файлов»: файлы кода есть, но короче порога, а не пути неверны.

    Повторный запуск с порогом в один токен и строку находит файлы, если они есть. Пустой результат
    и тогда значит, что jscpd ничего не видит (неверные пути, формат): настоящая ошибка проверки.
    """
    args = jscpd_args(project, language, min_lines="1", min_tokens="1")
    cut = args.index("--baseline")  # пробе baseline не нужен: файл долга ей трогать нельзя
    probe = [*jscpd_command(), *args[:cut], *args[cut + 2 :], "--fail-on-empty"]
    return run(probe, project).returncode == 0


def check_duplicates(project: Path, language: str) -> Result:
    result = Result()
    if not COLLECTORS[language].has_sources(project):
        result.note("Кода пока нет, проверка дублей пропущена.")
        return result
    ensure_empty_jscpd_baseline(project)
    extra = ["--fail-on-new-clones", "0", "--fail-on-empty"]
    done = run([*jscpd_command(), *jscpd_args(project, language), *extra], project)
    output = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr).strip()
    if (
        done.returncode != 0
        and "analyzed no files" in output
        and only_short_files(project, language)
    ):
        result.note(
            f"Код короче порога jscpd ({JSCPD_MIN_LINES} строк, {JSCPD_MIN_TOKENS} токенов): "
            "повторов быть не может, пока файлы не вырастут."
        )
    elif done.returncode != 0:
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
    "  allow_indirect_imports = True\n\n"
    "Уровни: верхний уровень может импортировать нижний, обратно нельзя:\n"
    "  [importlinter:contract:levels]\n  name = Уровни: ui выше services выше db\n  type = layers\n"
    "  layers =\n      ИМЯ_ПАКЕТА.ui\n      ИМЯ_ПАКЕТА.services\n      ИМЯ_ПАКЕТА.db\n\n"
    "Низкоуровневая библиотека (здесь sqlite3) подключается только в одном модуле;\n"
    "в [importlinter] нужна строка include_external_packages = True:\n"
    "  [importlinter:contract:sqlite-only-in-db]\n"
    "  name = Библиотека sqlite3 подключается только в ИМЯ_ПАКЕТА.db\n"
    "  type = forbidden\n  source_modules = ИМЯ_ПАКЕТА\n  forbidden_modules = sqlite3\n"
    "  ignore_imports = ИМЯ_ПАКЕТА.db -> sqlite3\n"
    "Так запрещено всем модулям, включая новые, кроме одного исключения в ignore_imports."
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
    project: Path,
    language: str,
    accept: Accept,
    report: Path | None = None,
    only_tests: bool = False,
    platform: str | None = None,
) -> Result:
    """Обновляет baseline; only_tests: только списки тестов и пропусков (репозиторий продукта).

    platform: система, на которой снят отчёт (windows или posix); по умолчанию система этого
    компьютера. Отчёт CI всегда снят на Linux, то есть posix.
    """
    result = Result()
    baseline = Baseline(project)
    if only_tests and report is None and language == "python":
        raise ToolError(
            "для baseline --only-tests нужен --report test-report.xml: пропуски и падения берутся "
            "из отчёта CI (артефакт test-report), а не из локального прогона. Скачайте отчёт: "
            "gh run download ИДЕНТИФИКАТОР -n test-report"
        )
    if report_is_partial(report):
        raise ToolError(
            "отказ: отчёт помечен как урезанный (parch-partial). baseline обновляется только по "
            'полному прогону: pytest -m "" --junitxml=test-report.xml'
        )
    outcomes = test_outcomes(project, language, report)
    if outcomes is None and language == "powershell":
        outcomes = {}  # PowerShell: тесты Pester не требуются (ADR-0010)
    if outcomes is None:
        result.fail(
            f"Нет отчёта о запуске тестов для {language}: без него пропуски по фактическому "
            "результату не посчитать. Передайте --report ФАЙЛ.",
            REPORT_HINT.get(language, ""),
        )
        return result
    failed_now = sorted(name for name, state in outcomes.items() if state == "failed")
    if failed_now:
        result.fail(
            f"Отказ: в запуске тестов есть упавшие ({len(failed_now)}): baseline по такому запуску "
            "не записывается, иначе он зафиксирует сломанное состояние как норму.",
            *shown(failed_now),
            "Почините тесты, запустите их заново и обновите baseline по чистому отчёту.",
        )
    skipped_now = skipped_ids(outcomes)
    if report is not None:
        gaps = report_gaps(project, language, outcomes, baseline, include_known=not accept.removed)
        fail_on_gaps(result, gaps)
    new_skipped = sorted(skipped_now - known_skipped(baseline, language, platform))
    if has_skipped_list(baseline, language, platform) and new_skipped and not accept.skips:
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
    if not only_tests:
        refuse_growth(
            result, baseline, "suppressions", language, suppressions, accept.suppressions,
            "--accept-suppressions", "число подавляющих комментариев",
        )  # fmt: skip
    if not only_tests and baseline.has("config", language) and not accept.config:
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
        has_sources = collected.has_sources(project) and not only_tests
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
    if not baseline.has("skipped_tests", language):
        baseline.set("skipped_tests", language, [])
    baseline.set("skipped_tests", platform_key(language, platform), sorted(skipped_now))
    if not only_tests:
        baseline.set("suppressions", language, suppressions)
        baseline.set("config", language, config)
    if collected is not None:
        baseline.set("tests", language, sorted(tests))
        if not only_tests:
            baseline.set("dead_code", language, sorted(dead))
        if collected.has_sources(project) and not only_tests:
            baseline.set("coverage", language, round(collected.coverage(project), 2))
            refresh = [*jscpd_command(), *jscpd_args(project, language), "--update-baseline"]
            updated = run(refresh, project)
            if updated.returncode != 0:
                raise ToolError("jscpd не смог обновить baseline:\n" + tail(updated, 800))
    baseline.save()
    if only_tests:
        result.note(
            f"baseline обновлён (только тесты и пропуски): тестов {len(tests)}, "
            f"пропусков {sum(skips.values())}, пропущенных запуском {len(skipped_now)}."
        )
        return result
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
            # Двойное приведение обходит проверку типов так же, как any.
            "double cast": r"\bas\s+(unknown|never)\s+as\b",
        },
        code_kinds=("explicit any", "double cast"),
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
            "ArchUnit WithoutRequiringPositiveResults": r"\bWithoutRequiringPositiveResults\b",
        },
        code_kinds=(
            "#pragma warning disable",
            "SuppressMessage",
            "#nullable disable",
            "ExcludeFromCodeCoverage",
            "ArchUnit WithoutRequiringPositiveResults",
        ),
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
            "nuget.config",
            "NuGet.Config",
            "NuGet.config",
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
        code_kinds=("SuppressMessage",),
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
ANALYZER_PACKAGE = re.compile(r"analyzers?\b|roslynator|stylecop|sonar", re.IGNORECASE)
CSPROJ_ELEMENTS = {
    "Nullable", "TreatWarningsAsErrors", "WarningsAsErrors", "WarningsNotAsErrors", "NoWarn",
    "AnalysisMode", "AnalysisLevel", "EnforceCodeStyleInBuild", "LangVersion", "IsTestProject",
    "CollectCoverage", "Threshold", "ThresholdType", "RunAnalyzers", "RunAnalyzersDuringBuild",
    "WarningLevel", "CodeAnalysisRuleSet", "ExcludeByAttribute", "ExcludeByFile", "Exclude",
    "GenerateDocumentationFile", "TargetFramework", "TargetFrameworks",
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
            analyzers = sorted(
                (e.get("Include") or e.get("Update") or "")
                for e in ElementTree.fromstring(text).iter()
                if e.tag.split("}")[-1] == "PackageReference"
                and ANALYZER_PACKAGE.search(e.get("Include") or e.get("Update") or "")
            )
            if analyzers:
                found[f"{rel}#анализаторы"] = digest_value(analyzers)
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
        elif language == "powershell":
            code_scope, comment_scope = blank_ps_comments_and_strings(text)
        elif language == "csharp":
            code_scope, comment_scope = blank_cs_comments_and_strings(text)
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


def parsed_root(path: Path, tags: set[str], kind: str) -> ElementTree.Element:
    """Корень XML-отчёта; чужой или повреждённый файл это ошибка, а не «тестов нет»."""
    root = ElementTree.parse(path).getroot()
    if local_name(root.tag) not in tags:
        raise ToolError(
            f"{path.name}: это не отчёт {kind} (корневой элемент {local_name(root.tag)})"
        )
    return root


def with_tests(outcomes: dict[str, str], path: Path) -> dict[str, str]:
    """Отчёт без единого теста нельзя принимать: пустой файл скрыл бы любые пропуски."""
    if not outcomes:
        raise ToolError(f"в отчёте {path.name} нет ни одного теста: отчёт пустой или повреждён")
    return outcomes


XDIST_GROUP_SUFFIX = re.compile(r"@[A-Za-z0-9_]+$")


def junit_outcomes(path: Path) -> dict[str, str]:
    """JUnit XML (pytest --junitxml, Pester JUnitXml): исход каждого теста."""
    outcomes: dict[str, str] = {}
    root = parsed_root(path, {"testsuites", "testsuite"}, "JUnit XML")
    for case in root.iter("testcase"):
        # pytest-xdist с --dist loadgroup дописывает к имени `@группа`: это не часть имени теста
        name = XDIST_GROUP_SUFFIX.sub("", case.get("name", ""))
        classname = case.get("classname", "")
        key = f"{classname}::{name}" if classname else name
        children = {local_name(child.tag) for child in case}
        state = "skipped" if "skipped" in children else "passed"
        if children & {"failure", "error"}:
            state = "failed"
        outcomes[key] = state
    return with_tests(outcomes, path)


def nunit_outcomes(path: Path) -> dict[str, str]:
    """NUnit 2.5 XML (Pester NUnitXml): исход каждого теста."""
    outcomes: dict[str, str] = {}
    for case in parsed_root(path, {"test-results"}, "NUnit XML").iter("test-case"):
        result = case.get("result", "").lower()
        state = "passed"
        if result in FAILED_NUNIT:
            state = "failed"
        elif result in SKIPPED_NUNIT or case.get("executed", "True").lower() == "false":
            state = "skipped"
        outcomes[case.get("name", "")] = state
    return with_tests(outcomes, path)


def pester_outcomes(path: Path) -> dict[str, str]:
    root = ElementTree.parse(path).getroot()
    return nunit_outcomes(path) if local_name(root.tag) == "test-results" else junit_outcomes(path)


def trx_outcomes(path: Path) -> dict[str, str]:
    """TRX (dotnet test --logger trx): исход каждого теста."""
    outcomes: dict[str, str] = {}
    for result in parsed_root(path, {"TestRun"}, "TRX").iter():
        if local_name(result.tag) != "UnitTestResult":
            continue
        outcome = result.get("outcome", "").lower()
        state = "passed"
        if outcome in FAILED_TRX:
            state = "failed"
        elif outcome in SKIPPED_TRX:
            state = "skipped"
        outcomes[result.get("testName", "")] = state
    return with_tests(outcomes, path)


def cs_trx_files(report: Path) -> list[Path]:
    if report.is_dir():
        return sorted(report.glob("*.trx"))
    return [report]


def trx_report_outcomes(report: Path) -> dict[str, str]:
    """Исходы тестов из файла TRX или из папки с файлами TRX (по одному на тестовый проект)."""
    files = cs_trx_files(report)
    if not files:
        raise ToolError(f"в {report} нет ни одного файла .trx. " + REPORT_HINT["csharp"])
    outcomes: dict[str, str] = {}
    for path in files:
        outcomes.update(trx_outcomes(path))
    return outcomes


def jest_json_outcomes(path: Path) -> dict[str, str]:
    """JSON-отчёт Jest и Vitest (--json, --reporter=json): исход каждого теста."""
    outcomes: dict[str, str] = {}
    root = as_dict(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(root.get("testResults"), list):
        raise ToolError(f"{path.name}: это не отчёт Jest/Vitest (нет списка testResults)")
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
    return with_tests(outcomes, path)


def any_report_outcomes(path: Path) -> dict[str, str]:
    """Исходы тестов из отчёта любого поддерживаемого формата (по содержимому, без языка)."""
    if path.is_dir():
        return trx_report_outcomes(path)
    if path.read_text(encoding="utf-8-sig", errors="replace").lstrip().startswith("{"):
        return jest_json_outcomes(path)
    tag = local_name(ElementTree.parse(path).getroot().tag)
    parsers = {
        "testsuites": junit_outcomes,
        "testsuite": junit_outcomes,
        "TestRun": trx_outcomes,
        "test-results": nunit_outcomes,
    }
    if tag not in parsers:
        raise ToolError(f"{path.name}: неизвестный формат отчёта (корневой элемент {tag})")
    return parsers[tag](path)


REPORT_PARSERS: dict[str, Callable[[Path], dict[str, str]]] = {
    "python": junit_outcomes,
    "powershell": pester_outcomes,
    "typescript": jest_json_outcomes,
    "csharp": trx_report_outcomes,
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


def platform_key(language: str, platform: str | None = None) -> str:
    """Ключ baseline для пропусков, зависящих от системы (тест только для Windows и т.п.)."""
    return f"{language}@{platform or ('windows' if os.name == 'nt' else 'posix')}"


def known_skipped(baseline: Baseline, language: str, platform: str | None = None) -> set[str]:
    """Пропущенные тесты, известные для всех систем и для этой системы."""
    return baseline.strings("skipped_tests", language) | baseline.strings(
        "skipped_tests", platform_key(language, platform)
    )


def has_skipped_list(baseline: Baseline, language: str, platform: str | None = None) -> bool:
    return baseline.has("skipped_tests", language) or baseline.has(
        "skipped_tests", platform_key(language, platform)
    )


def check_skips(
    project: Path, language: str, report: Path | None = None, partial: bool = False
) -> Result:
    """Пропуски считаются по фактическому результату запуска, текстовый поиск идёт дополнительно."""
    mismatch = partial_mismatch(report, partial)
    if mismatch:
        failed = Result()
        failed.fail(mismatch)
        return failed
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
    if report is not None and not partial:
        fail_on_gaps(result, report_gaps(project, language, outcomes, baseline))
    known = known_skipped(baseline, language)
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
        if not has_skipped_list(baseline, language):
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


# ---------- адаптер C# ----------

CS_REPORT_DIR = "test-results"
CS_PACKAGE_ELEMENTS = {
    "PackageReference",
    "PackageVersion",
    "GlobalPackageReference",
    "DotNetCliToolReference",
}
CS_NOT_MODULE_FILES = {"globalusings.cs", "assemblyinfo.cs", "globalsuppressions.cs"}
CS_DEAD_CODES = (
    "IDE0051", "IDE0052", "IDE0060", "IDE0005", "CS0169", "CS0219", "CS0414", "CS8019", "CA1823",
)  # fmt: skip
CS_WARNING = re.compile(
    r"^(?P<file>.+?)\(\d+,\d+\): warning (?P<code>[A-Z]+\d+): (?P<message>.*?)"
    r"(?: \(https?://[^)]*\))?(?: \[[^\]]+\])?$"
)
CS_NAMESPACE = re.compile(r"^\s*namespace\s+([\w.]+)\s*[;{]?\s*$", re.MULTILINE)
CS_RULE_CALL = re.compile(
    r"\b(?P<method>(?:DoNot|Not)?ResideInNamespace(?:Matching)?)\s*\(\s*"
    r"(?P<arg>@?\"(?:[^\"\\]|\\.)*\"|[^,)]*)"
)
CS_ARCH_EXAMPLE = (
    "Пример правила в тестовом проекте (ArchUnitNET):\n"
    '  Types().That().ResideInNamespace("Shop.Ui")\n'
    '      .Should().NotDependOnAny(Types().That().ResideInNamespace("Shop.Db")).Check(arch);'
)
DOTNET_ENV = {
    "DOTNET_CLI_UI_LANGUAGE": "en",
    "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    "DOTNET_CLI_USE_MSBUILD_SERVER": "0",
}
DOTNET_FLAGS = ["-nodeReuse:false", "-p:UseSharedCompilation=false"]


def blank_cs_comments_and_strings(text: str, keep_strings: bool = False) -> tuple[str, str]:
    """Код C# без комментариев (и без содержимого строк, если keep_strings=False).

    Второе значение: склеенный текст комментариев. Строки: обычные, буквальные `@"..."`,
    интерполированные `$"..."` (как обычные) и «сырые» (три кавычки); символьные литералы.
    Директивы препроцессора (`#pragma`) остаются кодом.
    """
    out: list[str] = []
    comments: list[str] = []
    blank = re.compile(r"[^\n]")
    i, n = 0, len(text)

    def hide(chunk: str) -> str:
        return chunk if keep_strings else blank.sub(" ", chunk)

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
        elif text.startswith('"""', i):
            quotes = len(text[i:]) - len(text[i:].lstrip('"'))
            end = text.find('"' * quotes, i + quotes)
            end = n if end == -1 else end + quotes
            out.append(text[i : i + quotes] + hide(text[i + quotes : end - quotes]) + '"' * quotes)
            i = end
        elif char == '"' or (char in "@$" and re.match(r'[@$]{1,2}"', text[i : i + 3])):
            prefix = re.match(r"[@$]{0,2}", text[i:])
            marker = prefix.group(0) if prefix else ""
            verbatim = "@" in marker
            j = i + len(marker) + 1
            while j < n:
                if verbatim and text[j] == '"' and text[j + 1 : j + 2] == '"':
                    j += 2
                    continue
                if text[j] == '"':
                    break
                if not verbatim and text[j] == "\\":
                    j += 1
                elif not verbatim and text[j] == "\n":
                    break
                j += 1
            end = min(j + 1, n)
            closed = end - i > len(marker) + 1 and text[end - 1] == '"'
            body = (
                text[i + len(marker) + 1 : end - 1] if closed else text[i + len(marker) + 1 : end]
            )
            out.append(marker + '"' + hide(body) + ('"' if closed else ""))
            i = end
        elif char == "'":
            j = i + 1
            while j < n and text[j] != "'" and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            end = min(j + 1, n)
            out.append("'" + blank.sub(" ", text[i + 1 : end - 1]) + "'")
            i = end
        else:
            out.append(char)
            i += 1
    return "".join(out), "\n".join(comments)


def cs_source_roots(project: Path) -> list[str]:
    return ["src"] if (project / "src").is_dir() else ["."]


def cs_is_source(name: str) -> bool:
    lower = name.lower()
    return (
        lower.endswith(".cs")
        and not lower.endswith((".g.cs", ".designer.cs", ".generated.cs"))
        and lower not in CS_NOT_MODULE_FILES
    )


def cs_skipped_dir(name: str) -> bool:
    return (
        name in SKIP_DIRS
        or name in {"bin", "obj", CS_REPORT_DIR, "TestResults"}
        or name.startswith(".")
    )


def cs_has_code(folder: Path) -> bool:
    for _current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not cs_skipped_dir(d)]
        if any(cs_is_source(f) for f in files):
            return True
    return False


def cs_modules(project: Path) -> list[str]:
    """Модули: папки внутри проекта .csproj и файлы кода рядом с ним (пути от корня проекта).

    Если папка внутри корня кода содержит .csproj, модулями считаются её подпапки и её файлы
    .cs, иначе сама папка.
    """
    modules: list[str] = []

    def add(entry: Path) -> None:
        modules.append(normalize_path(entry.relative_to(project).as_posix()))

    for root in cs_source_roots(project):
        base = project / root
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_dir():
                if cs_skipped_dir(entry.name):
                    continue
                if any(entry.glob("*.csproj")):
                    for inner in sorted(entry.iterdir()):
                        if inner.is_dir():
                            if not cs_skipped_dir(inner.name) and cs_has_code(inner):
                                add(inner)
                        elif cs_is_source(inner.name):
                            add(inner)
                elif cs_has_code(entry):
                    add(entry)
            elif cs_is_source(entry.name):
                add(entry)
    return modules


def cs_module_names(project: Path) -> list[str]:
    return [PurePosixPath(m).stem if m.endswith(".cs") else PurePosixPath(m).name
            for m in cs_modules(project)]  # fmt: skip


def cs_has_sources(project: Path) -> bool:
    return bool(cs_modules(project))


def cs_test_ids(project: Path, report: Path | None) -> list[str]:
    if report is None:
        raise ToolError(
            "для C# нужен отчёт о запуске тестов (--report ФАЙЛ_ИЛИ_ПАПКА): "
            "список тестов берётся из него. " + REPORT_HINT["csharp"]
        )
    return sorted(trx_report_outcomes(report))


def cs_manifest_packages(project: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in walk_files(project, (".csproj", ".props", ".targets", ".vbproj", ".fsproj")):
        rel = rel_of(project, path)
        for element in ElementTree.fromstring(path.read_text(encoding="utf-8-sig")).iter():
            if element.tag.split("}")[-1] not in CS_PACKAGE_ELEMENTS:
                continue
            name = element.get("Include") or element.get("Update")
            if name:
                found.append((rel, name))
    return found


def dotnet_exe() -> str:
    return shutil.which("dotnet") or "dotnet"


def cs_entry_point(project: Path) -> list[str]:
    """Решение или проект, который собирают: решение в корне, иначе единственный проект."""
    solutions = sorted([*project.glob("*.sln"), *project.glob("*.slnx")])
    if len(solutions) == 1:
        return [solutions[0].name]
    projects = sorted(project.glob("*.csproj"))
    if not solutions and len(projects) == 1:
        return [projects[0].name]
    raise ToolError(
        "в корне проекта должен быть ровно один файл решения (.sln) или проекта (.csproj), "
        f"найдено решений {len(solutions)}, проектов {len(projects)}"
    )


def dotnet_run(project: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return run([dotnet_exe(), *args, *DOTNET_FLAGS], project, DOTNET_ENV)


def cs_dead_code(project: Path) -> set[str]:
    """Находки анализаторов мёртвого кода (IDE0051/0052/0060/0005, CS0169/0414 и др.).

    Сборка идёт заново (`--no-incremental`): иначе анализаторы на неизменённом коде молчат и
    проверка была бы «зелёной» ничего не проверив. Предупреждения не считаются ошибками только
    здесь; обычная сборка проекта их ошибками считает (TreatWarningsAsErrors).
    """
    entry = cs_entry_point(project)
    args = ["build", *entry, "--no-incremental", "-p:TreatWarningsAsErrors=false",
            "-p:WarningsAsErrors=", "-v:q", "-clp:NoSummary"]  # fmt: skip
    done = dotnet_run(project, args)
    if done.returncode != 0:
        raise ToolError("dotnet build не прошёл (мёртвый код оценить нельзя):\n" + tail(done))
    findings: set[str] = set()
    for line in (done.stdout + "\n" + done.stderr).splitlines():
        match = CS_WARNING.match(line.strip())
        if not match or match["code"] not in CS_DEAD_CODES:
            continue
        file = Path(match["file"])
        try:
            rel = file.resolve().relative_to(project.resolve()).as_posix()
        except (ValueError, OSError):
            rel = normalize_path(match["file"])
        findings.add(f"roslyn|{match['code']}|{rel}|{match['message']}")
    return findings


def cs_coverage(project: Path) -> float:
    covered = valid = 0
    files = [
        p
        for p in (project / CS_REPORT_DIR).rglob("coverage.cobertura.xml")
        if "In" not in p.relative_to(project / CS_REPORT_DIR).parts
    ]
    if not files:
        raise ToolError(
            f"нет файлов coverage.cobertura.xml в {CS_REPORT_DIR}/: тесты нужно запускать с "
            f'покрытием (dotnet test --collect "XPlat Code Coverage" --results-directory '
            f"{CS_REPORT_DIR})"
        )
    for path in files:
        root = ElementTree.parse(path).getroot()
        covered += int(root.attrib.get("lines-covered", "0"))
        valid += int(root.attrib.get("lines-valid", "0"))
    return 100.0 * covered / valid if valid else 100.0


def cs_declared_namespaces(project: Path, files: list[Path]) -> dict[str, set[str]]:
    declared: dict[str, set[str]] = {}
    for path in files:
        code, _ = blank_cs_comments_and_strings(path.read_text(encoding="utf-8-sig"))
        declared[rel_of(project, path)] = set(CS_NAMESPACE.findall(code))
    return declared


def cs_module_files(project: Path, module: str) -> list[Path]:
    target = project / module
    if target.is_file():
        return [target]
    return [
        p for p in walk_files(project, (".cs",)) if target in p.parents and cs_is_source(p.name)
    ]


def cs_rule_calls(project: Path, result: Result) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """Файлы с правилами ArchUnitNET и все шаблоны пространств имён из их вызовов."""
    rules = RULES["csharp"]
    rule_files: list[Path] = []
    patterns: list[tuple[str, str, bool]] = []
    classes: list[str] = []
    for path in walk_files(project, (".cs",)):
        rel = rel_of(project, path)
        if not is_test_file(rules, rel):
            continue
        code, _ = blank_cs_comments_and_strings(path.read_text(encoding="utf-8-sig"), True)
        if "ArchRuleDefinition" not in code:
            continue
        rule_files.append(path)
        classes.extend(re.findall(r"\bclass\s+(\w+)", code))
        for call in CS_RULE_CALL.finditer(code):
            arg = call["arg"].strip()
            if not (arg.startswith('"') or arg.startswith('@"')):
                result.fail(
                    f"{rel}: в {call['method']}(...) не строка-литерал ({arg or 'пусто'}). "
                    "Имя пространства имён должно быть написано строкой, иначе проверить, что "
                    "правило находит типы, нельзя."
                )
                continue
            literal = cs_string_value(arg)
            patterns.append((rel, literal, call["method"].endswith("Matching")))
    return patterns, sorted(set(classes))


CS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}


def cs_string_value(literal: str) -> str:
    """Значение строкового литерала C#: обычного или буквального (@"a""b")."""
    if literal.startswith("@"):
        return literal[2:-1].replace('""', '"')
    return re.sub(r"\\(.)", lambda m: CS_ESCAPES.get(m.group(1), m.group(0)), literal[1:-1])


def cs_pattern_matches(pattern: str, regex: bool, namespace: str) -> bool:
    if not regex:
        return pattern == namespace
    try:
        return re.search(pattern, namespace) is not None
    except re.error:
        return False


def cs_architecture(project: Path) -> Result:
    result = Result()
    names = cs_module_names(project)
    if not names:
        result.note("Кода пока нет, правила архитектуры не проверяются.")
        return result
    patterns, classes = cs_rule_calls(project, result)
    if not result.ok:
        return result
    if not patterns:
        result.fail(
            "Нет ни одного правила архитектуры: в тестах не найдено ArchRuleDefinition с "
            'ResideInNamespace("..."). Правило без охвата не бывает зелёным.',
            CS_ARCH_EXAMPLE,
        )
        return result
    modules = cs_modules(project)
    declared = {
        rel: ns
        for rel, ns in cs_declared_namespaces(
            project, [p for m in modules for p in cs_module_files(project, m)]
        ).items()
    }
    every = sorted({ns for found in declared.values() for ns in found})
    dead = [
        f'{rel}: ResideInNamespace{"Matching" if regex else ""}("{pattern}"): '
        "такого пространства имён нет в коде (опечатка?). Такое правило ничего не проверяет."
        for rel, pattern, regex in patterns
        if not any(cs_pattern_matches(pattern, regex, ns) for ns in every)
    ]
    if dead:
        result.fail(
            "Правила архитектуры ссылаются на несуществующие пространства имён:", *shown(dead)
        )
        return result
    uncovered: list[str] = []
    for module in modules:
        if module.endswith(".cs"):
            continue
        spaces = {
            ns for p in cs_module_files(project, module) for ns in declared[rel_of(project, p)]
        }
        if spaces and not any(
            cs_pattern_matches(pattern, regex, ns)
            for _, pattern, regex in patterns
            for ns in spaces
        ):
            uncovered.append(f"{module} (пространства имён: {', '.join(sorted(spaces))})")
    if uncovered:
        result.fail(
            "Эти модули не охвачены ни одним правилом архитектуры:",
            *shown(uncovered),
            "Добавьте правило, где модуль указан через ResideInNamespace.",
        )
        return result
    return cs_run_architecture_tests(project, classes, len(patterns), result)


def cs_run_architecture_tests(
    project: Path, classes: list[str], patterns: int, result: Result
) -> Result:
    """Запускает именно тесты-правила и требует, чтобы хотя бы одно из них реально выполнилось."""
    entry = cs_entry_point(project)
    flt = "|".join(f"FullyQualifiedName~{name}" for name in classes)
    with tempfile.TemporaryDirectory() as tmp:
        args = ["test", *entry, "--filter", flt, "--logger", "trx", "--results-directory", tmp]
        done = dotnet_run(project, args)
        outcomes = trx_report_outcomes(Path(tmp)) if list(Path(tmp).glob("*.trx")) else {}
    if not outcomes:
        raise ToolError("тесты-правила архитектуры не запустились:\n" + tail(done))
    failed = sorted(name for name, state in outcomes.items() if state == "failed")
    if failed:
        result.fail("Правила архитектуры нарушены (тесты упали):", *shown(failed), tail(done, 1200))
        return result
    ran = sorted(name for name, state in outcomes.items() if state == "passed")
    if not ran:
        result.fail("Ни одно тестовое правило архитектуры не выполнилось (все пропущены).")
        return result
    result.note(f"Правил архитектуры (тестов): {len(ran)}; шаблонов пространств имён: {patterns}.")
    return result


# ---------- PowerShell: только тонкий «клей» (ADR-0010) ----------

PSA_VERSION = "1.25.0"
PSA_SETTINGS = "PSScriptAnalyzerSettings.psd1"
PSA_FAIL_SEVERITIES = {"error", "warning", "parseerror"}
PS_SCRIPT_EXTENSIONS = (".ps1", ".psm1")
PS_THRESHOLDS_HEADING = re.compile(
    r"^#{1,6}\s*(пороги тонкости powershell|powershell thin(ness)? thresholds)\s*$", re.IGNORECASE
)
PS_THRESHOLD_LINE = re.compile(r"^[-*]\s*`?(?P<key>\w+)`?\s*[:=]\s*(?P<value>\S+)\s*$")
PS_COMMENT_START = set(";(){},|&")
PS_RUNNER = """\
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$wanted = [version]'%(version)s'
$module = Get-Module -ListAvailable -Name PSScriptAnalyzer |
    Where-Object { $_.Version -eq $wanted } | Select-Object -First 1
if (-not $module) {
    $found = (Get-Module -ListAvailable -Name PSScriptAnalyzer |
        ForEach-Object { $_.Version.ToString() }) -join ', '
    Write-Output ('PARCH-MISSING ' + $found)
    exit 3
}
Import-Module -Name $module.Path -Force
$files = [string[]]@(Get-Content -LiteralPath '%(list)s' -Encoding UTF8)
$found = @()
foreach ($file in $files) {
    $arguments = @{ Path = $file }
    if ('%(settings)s' -ne '') { $arguments.Settings = '%(settings)s' }
    $tokens = $null
    $parseErrors = $null
    $parser = [System.Management.Automation.Language.Parser]
    [void]$parser::ParseFile($file, [ref]$tokens, [ref]$parseErrors)
    foreach ($problem in @($parseErrors)) {
        $found += [pscustomobject]@{
            File = $file; Rule = [string]$problem.ErrorId; Severity = 'ParseError'
            Line = $problem.Extent.StartLineNumber; Message = $problem.Message
        }
    }
    foreach ($record in @(Invoke-ScriptAnalyzer @arguments)) {
        $found += [pscustomobject]@{
            File = $file; Rule = $record.RuleName; Severity = [string]$record.Severity
            Line = $record.Line; Message = $record.Message
        }
    }
}
$report = @{
    version = $module.Version.ToString(); files = $files.Count; findings = @($found)
    shell = $PSVersionTable.PSVersion.ToString()
}
Write-Output ('PARCH-JSON ' + (ConvertTo-Json -InputObject $report -Depth 4 -Compress))
"""


def blank_ps_comments_and_strings(text: str, keep_strings: bool = False) -> tuple[str, str]:
    """Код PowerShell без комментариев (и без содержимого строк, если keep_strings=False).

    Второе значение: склеенный текст комментариев (`# ...` и `<# ... #>`). Строки: `'...'`,
    `"..."` (обратная кавычка и удвоенные кавычки экранируют) и here-строки `@'...'@`, `@"..."@`.
    """
    out: list[str] = []
    comments: list[str] = []
    blank = re.compile(r"[^\n]")
    i, n = 0, len(text)

    def hide(chunk: str) -> str:
        return chunk if keep_strings else blank.sub(" ", chunk)

    while i < n:
        char, following = text[i], text[i + 1] if i + 1 < n else ""
        previous = text[i - 1] if i else "\n"
        if char == "<" and following == "#":
            end = text.find("#>", i + 2)
            end = n if end == -1 else end + 2
            comments.append(text[i:end])
            out.append(blank.sub(" ", text[i:end]))
            i = end
        elif char == "#" and (previous.isspace() or previous in PS_COMMENT_START):
            end = text.find("\n", i)
            end = n if end == -1 else end
            comments.append(text[i:end])
            out.append(" " * (end - i))
            i = end
        elif char == "@" and following in "'\"" and re.match(r"@['\"][ \t]*\r?\n", text[i:]):
            quote = following
            close = re.compile(r"\r?\n" + quote + "@").search(text, i + 2)
            end = n if close is None else close.end()
            body_end = end if close is None else close.start()
            header = re.match(r"@['\"][ \t]*", text[i:])
            start = i + (header.end() if header else 2)
            out.append(text[i:start] + hide(text[start:body_end]) + text[body_end:end])
            i = end
        elif char in "'\"":
            j = i + 1
            while j < n:
                if text[j] == "`" and char == '"':
                    j += 2
                    continue
                if text[j] == char:
                    if text[j + 1 : j + 2] == char:
                        j += 2
                        continue
                    break
                j += 1
            end = min(j + 1, n)
            closed = end - i > 1 and text[end - 1] == char
            body = text[i + 1 : end - 1] if closed else text[i + 1 : end]
            out.append(char + hide(body) + (char if closed else ""))
            i = end
        else:
            out.append(char)
            i += 1
    return "".join(out), "\n".join(comments)


def ps_scripts(project: Path, extensions: tuple[str, ...]) -> list[Path]:
    return walk_files(project, extensions)


def ps_thresholds(project: Path) -> dict[str, int]:
    constitution = next((project / c for c in CONSTITUTIONS if (project / c).is_file()), None)
    if constitution is None:
        raise ToolError("нет CONSTITUTION.md: пороги тонкости PowerShell негде взять")
    found: dict[str, str] = {}
    inside = False
    for line in constitution.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if PS_THRESHOLDS_HEADING.match(stripped):
            inside = True
        elif inside and re.match(r"^#{1,6}\s", stripped):
            break
        elif inside and (match := PS_THRESHOLD_LINE.match(stripped)):
            found[match["key"].lower()] = match["value"]
    if not found:
        raise ToolError(
            "в CONSTITUTION.md нет раздела «Пороги тонкости PowerShell»: без порогов проверка не "
            "может работать (и не считается пройденной).\n" + PS_THRESHOLDS_EXAMPLE
        )
    values: dict[str, int] = {}
    for key in PS_THRESHOLD_KEYS:
        raw = found.get(key)
        if raw is None or not raw.isdigit():
            raise ToolError(
                f"порог {key} не задан целым числом в разделе «Пороги тонкости PowerShell» "
                f"(сейчас: {raw}).\n" + PS_THRESHOLDS_EXAMPLE
            )
        values[key] = int(raw)
    return values


PS_THRESHOLD_KEYS = (
    "max_commands",
    "max_loops",
    "max_conditions",
    "max_functions",
    "max_total_commands",
    "max_total_conditions",
)
PS_THRESHOLDS_EXAMPLE = (
    "Добавьте в docs/CONSTITUTION.md раздел:\n"
    "  ## Пороги тонкости PowerShell\n"
    "  - max_commands: 15\n"
    "  - max_loops: 0\n"
    "  - max_conditions: 2\n"
    "  - max_functions: 0\n"
    "  - max_total_commands: 30\n"
    "  - max_total_conditions: 4"
)
PS_THIN_HINT = (
    "PowerShell здесь только запуск программ. Перенесите логику в Python или C#, а в скрипте "
    "оставьте вызов программы и проверку кода выхода (ADR-0010)."
)
# Метрики считает штатный разбор PowerShell (AST), а не регулярные выражения: поэтому
# `$f = { цикл }; & $f`, `Set-Item function:X`, `a; b; c` в одной строке и строки в here-строках
# не обходят проверку.
PS_METRICS_RUNNER = """\
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$files = [string[]]@(Get-Content -LiteralPath '%(list)s' -Encoding UTF8)
$loopTypes = 'ForEachStatementAst', 'ForStatementAst', 'WhileStatementAst',
    'DoWhileStatementAst', 'DoUntilStatementAst'
$loopCommands = 'foreach-object', '%', 'foreach'
$conditionCommands = 'where-object', '?', 'where'
$dynamicCommands = 'invoke-expression', 'iex', 'add-type'
$listTypes = 'NamedBlockAst', 'StatementBlockAst'
$report = @()
foreach ($file in $files) {
    $tokens = $null
    $parseErrors = $null
    $parser = [System.Management.Automation.Language.Parser]
    $root = $parser::ParseFile($file, [ref]$tokens, [ref]$parseErrors)
    $entry = [ordered]@{
        File = $file; ParseErrors = @($parseErrors | ForEach-Object { $_.Message })
        Commands = 0; Loops = 0; Conditions = 0; Functions = 0; Details = @()
    }
    if (@($parseErrors).Count -eq 0) {
        foreach ($node in $root.FindAll({ $true }, $true)) {
            $type = $node.GetType().Name
            $parentType = if ($node.Parent) { $node.Parent.GetType().Name } else { '' }
            $line = $node.Extent.StartLineNumber
            if ($node -is [System.Management.Automation.Language.StatementAst] -and
                $listTypes -contains $parentType) { $entry.Commands++ }
            if ($type -eq 'PipelineAst') {
                $entry.Commands += [Math]::Max(0, $node.PipelineElements.Count - 1)
            }
            if ($loopTypes -contains $type) {
                $entry.Loops++; $entry.Details += "loop:$line"
            }
            if ($type -eq 'IfStatementAst') { $entry.Conditions += $node.Clauses.Count }
            if ($type -eq 'SwitchStatementAst') {
                $entry.Conditions += [Math]::Max(1, $node.Clauses.Count)
            }
            if ($type -eq 'TernaryExpressionAst') { $entry.Conditions++ }
            if ($type -in 'FunctionDefinitionAst', 'TypeDefinitionAst',
                'ConfigurationDefinitionAst') {
                $entry.Functions++
                $name = if ($node.Name) { $node.Name } else { '?' }
                $entry.Details += "function:${line}:$name"
            }
            if ($type -eq 'ScriptBlockExpressionAst' -and
                $parentType -notin 'CommandAst', 'CommandParameterAst') {
                $entry.Functions++; $entry.Details += "block:$line"
            }
            if ($type -eq 'VariableExpressionAst' -and
                $node.VariablePath.DriveName -eq 'function') {
                $entry.Functions++; $entry.Details += "drive:$line"
            }
            if ($type -eq 'InvokeMemberExpressionAst' -and
                $node.Member.Extent.Text -eq 'Create' -and
                $node.Expression.Extent.Text -match 'scriptblock') {
                $entry.Functions++; $entry.Details += "create:$line"
            }
            if ($type -eq 'CommandAst') {
                $name = ([string]$node.GetCommandName()).ToLowerInvariant()
                if ($loopCommands -contains $name) {
                    $entry.Loops++; $entry.Details += "loop:$line"
                }
                if ($conditionCommands -contains $name) { $entry.Conditions++ }
                if ($dynamicCommands -contains $name) {
                    $entry.Functions++; $entry.Details += "dynamic:${line}:$name"
                }
                foreach ($element in $node.CommandElements) {
                    if ($element.Extent.Text -match '^[''"]?function:') {
                        $entry.Functions++; $entry.Details += "drive:$line"
                    }
                }
            }
        }
    }
    $report += [pscustomobject]$entry
}
Write-Output ('PARCH-JSON ' + (ConvertTo-Json -InputObject @($report) -Depth 4 -Compress))
"""
PS_DETAIL_TEXT = {
    "loop": "цикл",
    "function": "собственная функция, класс или перечисление",
    "block": "блок кода вне вызова команды (в переменной, хеш-таблице и т.п.)",
    "drive": "определение функции через диск function:",
    "create": "[scriptblock]::Create",
    "dynamic": "динамический код",
}


def ps_metrics(project: Path, files: list[Path]) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "files.txt"
        listing.write_text("\n".join(str(p) for p in files), encoding="utf-8")
        runner = Path(tmp) / "metrics.ps1"
        script = PS_METRICS_RUNNER.replace("%(list)s", str(listing).replace("'", "''"))
        runner.write_text(script, encoding="utf-8-sig")
        argv = [powershell_exe(), "-NoProfile", "-NonInteractive"]
        if os.name == "nt":
            argv += ["-ExecutionPolicy", "Bypass"]
        done = run([*argv, "-File", str(runner)], project)
    payload = next((x for x in done.stdout.splitlines() if x.startswith("PARCH-JSON ")), None)
    if done.returncode != 0 or payload is None:
        raise ToolError("не удалось разобрать скрипты PowerShell:\n" + tail(done))
    return [as_dict(x) for x in as_list(json.loads(payload.removeprefix("PARCH-JSON ")))]


def ps_detail_lines(details: object, wanted: set[str]) -> str:
    kinds: dict[str, list[str]] = {}
    for item in as_strings(details):
        kind, _, rest = item.partition(":")
        if kind in wanted:
            kinds.setdefault(kind, []).append(rest.replace(":", " "))
    return "; ".join(
        f"{PS_DETAIL_TEXT.get(kind, kind)} (строка {', '.join(places[:3])})"
        for kind, places in kinds.items()
    )


def check_thin(project: Path, language: str) -> Result:
    result = Result()
    if language != "powershell":
        raise ToolError("проверка thin относится только к PowerShell")
    limits = ps_thresholds(project)
    rules = RULES["powershell"]
    scripts = [
        p
        for p in ps_scripts(project, PS_SCRIPT_EXTENSIONS)
        if not is_test_file(rules, rel_of(project, p))
    ]
    if not scripts:
        result.note("Скриптов PowerShell пока нет, проверка тонкости не нужна.")
        return result
    heavy: list[str] = []
    total_commands = total_conditions = 0
    for entry in ps_metrics(project, scripts):
        rel = rel_of(project, Path(str(entry["File"])))
        errors = as_strings(entry.get("ParseErrors"))
        if errors:
            heavy.append(f"{rel}: скрипт не разбирается ({errors[0]}), тонкость не измерить")
            continue
        commands, loops = int(str(entry["Commands"])), int(str(entry["Loops"]))
        conditions, functions = int(str(entry["Conditions"])), int(str(entry["Functions"]))
        total_commands += commands
        total_conditions += conditions
        for label, value, key, kinds in (
            ("команд", commands, "max_commands", set[str]()),
            ("циклов", loops, "max_loops", {"loop"}),
            ("условий", conditions, "max_conditions", set[str]()),
            (
                "собственных функций и блоков кода",
                functions,
                "max_functions",
                {"function", "block", "drive", "create", "dynamic"},
            ),
        ):
            if value > limits[key]:
                extra = ps_detail_lines(entry.get("Details"), kinds)
                heavy.append(
                    f"{rel}: {label} {value}, порог {limits[key]} ({key})"
                    + (f"; найдено: {extra}" if extra else "")
                )
    if total_commands > limits["max_total_commands"]:
        heavy.append(
            f"весь PowerShell проекта: команд {total_commands}, общий бюджет "
            f"{limits['max_total_commands']} (max_total_commands); логика, разнесённая по "
            "нескольким маленьким скриптам, считается вместе"
        )
    if total_conditions > limits["max_total_conditions"]:
        heavy.append(
            f"весь PowerShell проекта: условий {total_conditions}, общий бюджет "
            f"{limits['max_total_conditions']} (max_total_conditions)"
        )
    if heavy:
        result.fail("Скрипты PowerShell не «тонкие»:", *shown(heavy), PS_THIN_HINT)
        return result
    result.note(
        f"Скриптов PowerShell: {len(scripts)}; команд {total_commands} "
        f"(бюджет {limits['max_total_commands']}), условий {total_conditions} "
        f"(бюджет {limits['max_total_conditions']}); все в пределах порогов."
    )
    return result


def powershell_exe() -> str:
    """Только PowerShell 7 (`pwsh`): Windows PowerShell 5.1 не используется (ADR-0010)."""
    found = shutil.which("pwsh")
    if found is None:
        raise ToolError(
            "не найден PowerShell 7 (pwsh). Windows PowerShell 5.1 не подходит: "
            "установите PowerShell 7 (на Windows: winget install Microsoft.PowerShell)"
        )
    return found


def psa_findings(project: Path, files: list[Path]) -> tuple[int, list[dict[str, object]], str]:
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "files.txt"
        listing.write_text("\n".join(str(p) for p in files), encoding="utf-8")
        settings = project / PSA_SETTINGS
        script = PS_RUNNER % {
            "version": PSA_VERSION,
            "list": str(listing).replace("'", "''"),
            "settings": str(settings).replace("'", "''") if settings.is_file() else "",
        }
        runner = Path(tmp) / "run.ps1"
        runner.write_text(script, encoding="utf-8-sig")
        argv = [powershell_exe(), "-NoProfile", "-NonInteractive"]
        if os.name == "nt":
            argv += ["-ExecutionPolicy", "Bypass"]
        done = run([*argv, "-File", str(runner)], project)
    lines = [line for line in done.stdout.splitlines() if line.startswith("PARCH-")]
    if any(line.startswith("PARCH-MISSING") for line in lines):
        installed = lines[0].removeprefix("PARCH-MISSING").strip() or "не установлен"
        raise ToolError(
            f"нужен PSScriptAnalyzer {PSA_VERSION} (найдено: {installed}). Установите: "
            f"Install-Module PSScriptAnalyzer -RequiredVersion {PSA_VERSION} -Scope CurrentUser"
        )
    payload = next((x for x in lines if x.startswith("PARCH-JSON ")), None)
    if done.returncode != 0 or payload is None:
        raise ToolError("PSScriptAnalyzer не отработал:\n" + tail(done))
    data = as_dict(json.loads(payload.removeprefix("PARCH-JSON ")))
    findings = [as_dict(x) for x in as_list(data.get("findings"))]
    return int(str(data.get("files", 0))), findings, str(data.get("shell", ""))


def check_psscriptanalyzer(project: Path, language: str) -> Result:
    result = Result()
    if language != "powershell":
        raise ToolError("проверка psscriptanalyzer относится только к PowerShell")
    if not ps_scripts(project, PS_SCRIPT_EXTENSIONS):
        result.note("Скриптов PowerShell пока нет, PSScriptAnalyzer не запускался.")
        return result
    files = ps_scripts(project, (".ps1", ".psm1", ".psd1"))
    scanned, findings, shell_version = psa_findings(project, files)
    if scanned != len(files):
        raise ToolError(f"PSScriptAnalyzer проверил {scanned} файлов из {len(files)}")
    bad = sorted(
        {
            f"{rel_of(project, Path(str(f['File'])))}:{f.get('Line') or 0}: {f['Rule']} "
            f"({f['Severity']}): {f['Message']}"
            for f in findings
            if str(f.get("Severity", "")).lower() in PSA_FAIL_SEVERITIES
        }
    )
    if bad:
        result.fail(
            f"PSScriptAnalyzer нашёл {len(bad)} замечаний (Error и Warning не допускаются):",
            *shown(bad),
            "Исправьте причину. Подавление (SuppressMessage) считается и не должно расти.",
        )
        return result
    result.note(
        f"PSScriptAnalyzer {PSA_VERSION} на PowerShell {shell_version}: файлов {scanned}, "
        "замечаний нет."
    )
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
    "csharp": Collector(
        "nuget",
        "csharp",
        cs_test_ids,
        cs_modules,
        cs_module_names,
        cs_manifest_packages,
        cs_dead_code,
        cs_coverage,
        cs_has_sources,
        cs_architecture,
    ),
}

# ---------- проверка standard: стоимость CI и бюджет текста (STANDARD.md 7.2, 11, 12) ----------

STANDARD_VERSION = "1.3"
STANDARD_MAX_TIMEOUT = 20
# Дольше 20 минут только по принятому ADR с записью `timeout-minutes: N`; больше 60 нельзя.
STANDARD_HARD_TIMEOUT = 60
STANDARD_BASE_RUNNER = "ubuntu-latest"
# Единственный workflow, которому можно запускаться на push в main: пересчёт состояния после
# слияния (STATUS.md, features.json), без тестов (STANDARD.md 7.2, п. 6).
STATE_WORKFLOW = "state"
AGENTS_MAX_LINES = 150
STANDARD_NOT_YET = "карточка соответствия P1–P12 в STATUS.md и показ изменений версии стандарта"
WORKFLOW_KEY = re.compile(r"^(?P<indent> *)(?P<key>[\w\"'-]+):[ \t]*(?P<value>.*)$")


def yaml_children(lines: list[str], start: int) -> list[str]:
    """Строки, вложенные глубже заголовка в строке `start` (пустые и комментарии пропускаются)."""
    head = len(lines[start]) - len(lines[start].lstrip(" "))
    found: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) <= head:
            break
        found.append(line)
    return found


def yaml_keys(lines: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Ключи верхнего уровня строк: имя -> (значение в строке, вложенные строки)."""
    keys: dict[str, tuple[str, list[str]]] = {}
    clean = [re.sub(r"\s+#.*$", "", line.rstrip()) for line in lines]
    clean = [line for line in clean if line.strip() and not line.lstrip().startswith("#")]
    if not clean:
        return keys
    top = min(len(line) - len(line.lstrip(" ")) for line in clean)
    for index, line in enumerate(clean):
        match = WORKFLOW_KEY.match(line)
        if match is None or len(match["indent"]) != top:
            continue
        name = match["key"].strip("\"'")
        keys[name] = (match["value"].strip(), yaml_children(clean, index))
    return keys


def accepted_adr_text(project: Path) -> str:
    """Текст принятых ADR: только они разрешают нестандартные раннеры и матрицы."""
    texts: list[str] = []
    for path in sorted((project / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^- Статус:\s*accepted\b", text, re.MULTILINE | re.IGNORECASE):
            texts.append(text)
    return "\n".join(texts)


def workflow_triggers(on_value: tuple[str, list[str]]) -> set[str]:
    inline, children = on_value
    if inline.startswith("["):
        return {x.strip().strip("\"'") for x in inline.strip("[]").split(",") if x.strip()}
    if inline and not inline.startswith("{"):
        return {inline.strip("\"'")}
    return set(yaml_keys(children))


CIRCLE_BASE_CLASSES = {"small", "medium"}  # Docker; остальное только по принятому ADR
CIRCLE_HEAVY = re.compile(r"\b(pytest|dotnet test|npm (?:run )?test)\b")


def circleci_problems(path: Path, adr_text: str) -> list[str]:
    """Правила стоимости 7.2 для конфига CircleCI.

    Расписание, не-Linux исполнители, большие классы, пределы времени. Автоотмена устаревших
    прогонов настраивается в проекте CircleCI, из конфига её не видно (есть пометка в выводе).
    """
    rel = f".circleci/{path.name}"
    text = "\n".join(
        re.sub(r"(^|\s)#.*", "", line) for line in path.read_text(encoding="utf-8").splitlines()
    )
    problems: list[str] = []
    if re.search(r"^\s*(?:-\s*)?schedule:", text, re.M) and "schedule" not in adr_text.lower():
        problems.append(
            f"{rel}: запуск по расписанию. CI запускается только на PR (и пересчёт табло на "
            "main): расписание тратит кредиты без отправки. Только через ADR с оценкой "
            "кредитов (ADR-0011, ADR-0020)."
        )
    named = {
        *re.findall(r"executor:\s*(win/[\w.-]+)", text),
        *re.findall(r"image:\s*['\"]?(windows-[\w.:-]+)", text),
        *re.findall(r"resource_class:\s*['\"]?((?:windows|macos)[\w.-]*)", text),
    }
    if re.search(r"^\s*(macos|xcode):", text, re.M) or any(n.startswith("macos") for n in named):
        named.add("macos")
    for name in sorted(named):
        if name not in adr_text:
            problems.append(
                f"{rel}: исполнитель {name} не назван ни в одном принятом ADR. Windows дороже "
                "Linux в 4 раза, macOS в 10 и больше. Оформите ADR с оценкой кредитов и "
                "назовите в нём этот исполнитель."
            )
    for resource_class in sorted(set(re.findall(r"resource_class:\s*['\"]?([\w.-]+)", text))):
        if resource_class in CIRCLE_BASE_CLASSES or resource_class.startswith(("windows", "macos")):
            continue
        if f"`{resource_class}`" not in adr_text:
            problems.append(
                f"{rel}: класс ресурсов {resource_class} не назван в принятом ADR (малый и "
                "средний допустимы без ADR; большой стоит вдвое дороже). Назовите его в ADR "
                "с оценкой кредитов."
            )
    for step in re.split(r"\n\s*- ", text):
        limited = "no_output_timeout" in step or "timeout " in step
        if CIRCLE_HEAVY.search(step) and not limited:
            first = step.strip().splitlines()[0] if step.strip() else ""
            problems.append(
                f"{rel}: долгий шаг без предела времени ({first[:60]}): добавьте "
                "no_output_timeout и команду timeout вокруг тестов. У CircleCI нет "
                "timeout-minutes на задание, зависший тест идёт до часа."
            )
    # Ключ записи (ADR-0022): только в задании state и только после тестов проекта.
    jobs_text = (
        text.split("\njobs:\n", 1)[1].split("\nworkflows:\n", 1)[0] if "\njobs:\n" in text else ""
    )
    for chunk in re.split(r"(?m)^  (?=[\w-]+:[ \t]*$)", jobs_text):
        if "add_ssh_keys" not in chunk:
            continue
        job = chunk.split(":", 1)[0].strip()
        key_at = chunk.index("add_ssh_keys")
        tests_after_key = any(m.start() > key_at for m in CIRCLE_HEAVY.finditer(chunk))
        if job != "state":
            problems.append(
                f"{rel}: add_ssh_keys в задании {job}: ключ записи подключается только в задании "
                "state, иначе проверки веток PR получили бы право писать в репозиторий."
            )
        elif tests_after_key:  # любой шаг тестов после ключа, а не только первый
            problems.append(
                f"{rel}: в задании state ключ записи (add_ssh_keys) подключён до тестов проекта: "
                "код проекта выполнился бы с ключом. Поставьте add_ssh_keys после шага тестов."
            )
    # Итоговый `check` (ADR-0022): единственная обязательная проверка main,
    # он обязан требовать все языковые задания.
    languages = "python|typescript|csharp|powershell"  # `check-text` продукта не языковое
    defined = sorted(set(re.findall(rf"^  (check-(?:{languages})):[ \t]*$", text, re.M)))
    if defined:
        found = re.search(
            r"^[ \t]+- check:\n[ \t]+requires:\n((?:[ \t]+- [\w-]+\n)+)", text + "\n", re.M
        )
        required = re.findall(r"- ([\w-]+)", found[1]) if found else []
        missing = [name for name in defined if name not in required]
        if found is None:
            problems.append(
                f"{rel}: нет итогового задания check с requires на {', '.join(defined)}: "
                "обязательная проверка main (ci/circleci: check) не зависела бы от них (ADR-0022)."
            )
        elif missing:
            problems.append(
                f"{rel}: итоговый check не требует {', '.join(missing)}: красный язык не остановит "
                "слияние (в защите main обязателен только check). Добавьте задание в requires."
            )
    return problems


def standard_workflow_problems(path: Path, adr_text: str) -> list[str]:
    """Нарушения правил 7.2 в одном workflow: таймаут, отмена, триггеры, раннеры, матрицы."""
    rel = f".github/workflows/{path.name}"
    top = yaml_keys(path.read_text(encoding="utf-8").splitlines())
    problems: list[str] = []
    if "jobs" not in top or not top["jobs"][1]:
        return [
            f"{rel}: не найден раздел jobs, workflow не удалось разобрать "
            "(проверка не может считать его безопасным)"
        ]
    triggers = workflow_triggers(top.get("on", top.get("true", ("", []))))
    allowed: set[str] = {"pull_request"}
    if path.stem == STATE_WORKFLOW:
        allowed.add("push")
    extra = sorted(triggers - allowed)
    if extra or not triggers:
        problems.append(
            f"{rel}: запуск на {', '.join(extra) or 'неизвестном событии'}. "
            "CI запускается только на pull_request: тесты на коммите уже прошли в PR, "
            "повторный прогон тратит квоту. Для пересчёта состояния после слияния допускается "
            f"только workflow {STATE_WORKFLOW}.yml. Другой запуск возможен только через ADR "
            "с оценкой минут квоты (ADR-0011)."
        )
    concurrency = top.get("concurrency")
    cancels = concurrency is not None and any(
        re.match(r"\s*cancel-in-progress:\s*true\s*$", line) for line in concurrency[1]
    )
    if not cancels:
        problems.append(
            f"{rel}: нет concurrency с cancel-in-progress: true. Без отмены каждый новый push "
            "запускает полный прогон поверх старого. Добавьте на верхнем уровне: "
            "concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}."
        )
    for job, (_, body) in yaml_keys(top["jobs"][1]).items():
        fields = yaml_keys(body)
        timeout = fields.get("timeout-minutes", ("", []))[0]
        minutes = int(timeout) if timeout.isdigit() else 0
        by_adr = (
            STANDARD_MAX_TIMEOUT < minutes <= STANDARD_HARD_TIMEOUT
            and f"timeout-minutes: {minutes}" in adr_text
        )
        if not (1 <= minutes <= STANDARD_MAX_TIMEOUT or by_adr):
            problems.append(
                f"{rel}, задание {job}: timeout-minutes {timeout or 'не задан'} "
                f"(нужно число от 1 до {STANDARD_MAX_TIMEOUT}; больше только если принятый ADR "
                f"называет это значение записью `timeout-minutes: N`, но не больше "
                f"{STANDARD_HARD_TIMEOUT}). По умолчанию GitHub ждёт 360 минут, и один "
                "зависший тест стоит шесть часов квоты."
            )
        runner = fields.get("runs-on", ("", []))[0].strip("\"'")
        if "uses" not in fields and not runner:
            problems.append(f"{rel}, задание {job}: не задан runs-on, раннер неизвестен.")
        elif runner and runner != STANDARD_BASE_RUNNER and runner not in adr_text:
            problems.append(
                f"{rel}, задание {job}: раннер {runner} не разрешён ни одним принятым ADR. "
                "Windows считается в квоте как x2, macOS как x10. Если раннер нужен, оформите ADR "
                "с оценкой минут квоты за один прогон и назовите в нём этот раннер; иначе "
                f"используйте {STANDARD_BASE_RUNNER}."
            )
        if (
            "strategy" in fields
            and any("matrix" in line for line in fields["strategy"][1])
            and not (path.name in adr_text and re.search("matrix|матриц", adr_text, re.IGNORECASE))
        ):
            problems.append(
                f"{rel}, задание {job}: матрица умножает минуты квоты. Она допускается, только "
                f"если принятый ADR называет файл {path.name} и объясняет матрицу с оценкой "
                "стоимости."
            )
    return problems


TARGET_OS_HEADING = re.compile(r"^#{1,6}\s*целевая ос\s*$", re.IGNORECASE)


def target_os_problem(project: Path) -> str | None:
    """В CONSTITUTION.md должна быть записана целевая ОС (STANDARD.md, 7.2, правило 2)."""
    constitution = next((project / c for c in CONSTITUTIONS if (project / c).is_file()), None)
    if constitution is None:
        return None
    inside = False
    for line in constitution.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if TARGET_OS_HEADING.match(stripped):
            inside = True
        elif inside and re.match(r"^#{1,6}\s", stripped):
            break
        elif inside and stripped and not stripped.startswith((">", "(", "{{")):
            return None
    return (
        f"{constitution.name}: не записана целевая ОС (раздел «Целевая ОС»). На ней работает "
        "исполнитель и проверяет проект бесплатно, а CI на Linux проверяет переносимость; без "
        "записи непонятно, какую систему считать главной. Спросите владельца и запишите "
        "(Windows 11, macOS или Linux)."
    )


INCIDENT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<block>[A-Za-z0-9]+)-.+\.md$")


def blocked_without_incident_problems(project: Path) -> list[str]:
    """Блок в статусе blocked или stuck обязан иметь отчёт state/incidents/ДАТА-БЛОК-*.md."""
    path = project / "state" / "features.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"{path.name}: не читается как JSON"]
    folder = project / "state" / "incidents"
    files: list[Path] = sorted(folder.iterdir()) if folder.is_dir() else []
    reported = {m["block"] for m in (INCIDENT_NAME.match(f.name) for f in files) if m}
    problems: list[str] = []
    for entry in as_list(as_dict(data).get("features")):
        item = as_dict(entry)
        if item.get("status") in {"blocked", "stuck"}:
            block = str(item.get("id", "?"))
            if block not in reported:
                problems.append(
                    f"state/features.json: блок {block} в статусе «{item['status']}» без отчёта "
                    f"state/incidents/ГГГГ-ММ-ДД-{block}-причина.md. Запишите, что случилось и "
                    "чего ждёте; если решения ждёте от владельца, поставьте статус waiting_owner."
                )
    return problems


INCIDENT_BUDGET_DEFAULT = 2  # STANDARD.md, 6.6: бюджет инцидентов на блок по умолчанию
INCIDENT_BUDGET_LINE = re.compile(r"^\s*Бюджет инцидентов на блок:\s*(\d+)\s*$", re.MULTILINE)
NO_BLOCK = "NONE"  # в имени отчёта: работа вне блока
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMPACT_EVIDENCE = re.compile(r"\bG\d+\b|ни один", re.IGNORECASE)
SEARCH_LINK = re.compile(
    r"https?://|#\d+|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"  # ссылка, PR, коммит
    r"|\d{4}-\d{2}-\d{2}-[\w-]+\.md"  # прошлый отчёт об инциденте
)
SEARCH_QUERY = re.compile(
    r"«[^»\s][^»]*»|`[^`\s][^`]*`|\"[^\"\s][^\"]*\""
)  # поисковый запрос в кавычках
SEARCH_LINE = re.compile(r"^\s*[-*]\s*(?P<label>[^:]{2,160}):\s*(?P<body>\S.*)$")
SEARCH_SOURCES = (
    ("code", re.compile(r"по коду", re.IGNORECASE)),
    ("modules", re.compile(r"реестр модулей|каталог возможностей", re.IGNORECASE)),
    ("history", re.compile(r"git log|истори[яи] изменений", re.IGNORECASE)),
    ("past", re.compile(r"прошл\w*\s+(?:отч[её]т|инцидент)|урок|lessons", re.IGNORECASE)),
)
IMPACT_HEADING = "Влияние на цель"
SEARCH_HEADING = "Где искал"
INCIDENT_SERVICE_FILES = {".gitkeep", "readme.md"}


def incident_budget(project: Path, override: object = None) -> int:
    """Бюджет инцидентов блока: свой (`incident_budget` в features.json) или из CONSTITUTION.md."""
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    for name in CONSTITUTIONS:
        path = project / name
        if path.is_file():
            found = INCIDENT_BUDGET_LINE.search(path.read_text(encoding="utf-8"))
            if found:
                return int(found.group(1))
    return INCIDENT_BUDGET_DEFAULT


def incident_files(project: Path) -> list[Path]:
    folder = project / "state" / "incidents"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.name.lower() not in INCIDENT_SERVICE_FILES)


def markdown_section(text: str, heading: str) -> str | None:
    """Текст раздела `## heading` без HTML-комментариев (None, если раздела нет)."""
    lines = HTML_COMMENT.sub("", text).splitlines()
    wanted = heading.lower()
    for index, line in enumerate(lines):
        if re.match(r"^#{1,6}\s+", line) and line.lstrip("# ").strip().lower() == wanted:
            body: list[str] = []
            for follow in lines[index + 1 :]:
                if re.match(r"^#{1,6}\s", follow):
                    break
                body.append(follow)
            return "\n".join(body).strip()
    return None


def search_sources(section: str) -> set[str]:
    """Источники раздела «Где искал», у которых есть запрос или ссылка (пустая метка не в счёт)."""
    found: set[str] = set()
    for line in section.splitlines():
        match = SEARCH_LINE.match(line)
        if not match:
            continue
        label, body = match["label"], match["body"]
        for source, pattern in SEARCH_SOURCES:
            if not pattern.search(label):
                continue
            has_query = bool(SEARCH_QUERY.search(body))
            if has_query or (source != "code" and SEARCH_LINK.search(body)):
                found.add(source)
    return found


def incident_report_problems(project: Path, block_ids: set[str] | None) -> list[str]:
    """Отчёты state/incidents/: имя, блок, «Влияние на цель» и «Что нашёл в истории» (6.3, 6.5a)."""
    problems: list[str] = []
    for path in incident_files(project):
        rel = f"state/incidents/{path.name}"
        found = INCIDENT_NAME.match(path.name)
        if not found:
            problems.append(
                f"{rel}: имя должно быть ГГГГ-ММ-ДД-БЛОК-слово.md "
                f"(БЛОК, например F9, или {NO_BLOCK}, "
                "если работа вне блока)"
            )
            continue
        block = found["block"]
        if block != NO_BLOCK and block_ids is not None and block not in block_ids:
            problems.append(
                f"{rel}: блока {block} нет в state/features.json (вне блока: {NO_BLOCK})"
            )
        text = path.read_text(encoding="utf-8")
        impact = markdown_section(text, IMPACT_HEADING)
        if not impact or not IMPACT_EVIDENCE.search(impact):
            problems.append(
                f"{rel}: раздел «{IMPACT_HEADING}» пуст или без критерия цели (G1…) "
                "и без слов «ни один»: "
                "прежде чем чинить, из отчёта должно быть видно, нужно ли чинить"
            )
        searched = markdown_section(text, SEARCH_HEADING)
        sources = search_sources(searched or "")
        if "code" not in sources or len(sources) < 2:
            problems.append(
                f"{rel}: раздел «{SEARCH_HEADING}» неполный: нужен поиск по коду с запросом "
                "(строка «Поиск по коду: «запрос»») и ещё хотя бы один источник с запросом "
                "или ссылкой "
                "(реестр модулей или каталог возможностей, история изменений `git log -S`, прошлые "
                "отчёты и уроки). «Не нашёл» засчитывается только с перечнем мест и запросов"
            )
    return problems


def incident_problems_by_file(project: Path, block_ids: set[str] | None) -> dict[str, str]:
    """Нарушения отчётов по файлам: ключ `incident:<имя файла>`, для долга проекта."""
    grouped: dict[str, list[str]] = {}
    prefix = "state/incidents/"
    for message in incident_report_problems(project, block_ids):
        name = message.split(": ", 1)[0].removeprefix(prefix)
        grouped.setdefault(name, []).append(message)
    return {f"incident:{name}": "; ".join(items) for name, items in grouped.items()}


def incident_budget_problems(project: Path) -> list[str]:
    """Блок, исчерпавший бюджет инцидентов, обязан иметь статус stuck (его ставит PR с отчётом)."""
    path = project / "state" / "features.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    counts: dict[str, int] = {}
    for report in incident_files(project):
        found = INCIDENT_NAME.match(report.name)
        if found and found["block"] != NO_BLOCK:
            counts[found["block"]] = counts.get(found["block"], 0) + 1
    problems: list[str] = []
    for entry in as_list(as_dict(data).get("features")):
        item = as_dict(entry)
        block = str(item.get("id", "?"))
        budget = incident_budget(project, item.get("incident_budget"))
        if counts.get(block, 0) >= budget and item.get("status") not in {
            "stuck",
            "done",
            "dropped",
        }:
            problems.append(
                f"state/features.json: у блока {block} инцидентов {counts[block]} "
                f"при бюджете {budget}, "
                "а статус не «stuck». Статус ставит тот же PR, что добавляет отчёт; "
                "снять его может только "
                "решение владельца (статус или incident_budget блока в features.json)"
            )
    return problems


def block_ids_of(project: Path) -> set[str] | None:
    path = project / "state" / "features.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {str(as_dict(e).get("id", "")) for e in as_list(as_dict(data).get("features"))}


def check_standard(
    project: Path, language: str, update: bool = False, accept_new: bool = False
) -> Result:
    """Соответствие стандарту: стоимость CI, бюджет текста, отчёты, состав проекта (F14)."""
    del language
    result = Result()
    problems: list[str] = []
    incidents_in_debt = False
    try:
        import parch_standard  # лежит рядом (.github/parch/), копируется вместе с этим файлом
    except ImportError:
        problems.append(
            "нет файла .github/parch/parch_standard.py рядом с parch_ci.py: правила состава "
            "проекта не работают; скопируйте его из плагина (или повторите init-project)"
        )
        composition_notes: list[str] = []
    else:
        # Подключённый проект: отчёты об инцидентах идут через долг (исторические отчёты записывает
        # владелец, новые проверяются полностью); неподключённый проверяется как раньше, ниже.
        managed = parch_standard.is_managed(project)
        extra = incident_problems_by_file(project, block_ids_of(project)) if managed else None
        pr_body = os.environ.get("PARCH_PR_BODY")
        # CircleCI (ADR-0022): описания PR там нет, «Основания» берутся из сообщений коммитов ветки
        # (`git log <PARCH_BASE_REF>..HEAD`); у проектов на Actions по-прежнему PARCH_PR_BODY.
        base_ref = os.environ.get("PARCH_BASE_REF")
        commits = (
            parch_standard.commit_messages(project, base_ref)
            if base_ref and pr_body is None
            else None
        )
        changed = (
            parch_standard.changed_files(project, base_ref)
            if commits is not None and base_ref
            else None
        )
        composition_problems, composition_notes = parch_standard.check(
            project, update, accept_new, extra, pr_body, commits, changed
        )
        problems.extend(composition_problems)
        if (
            pr_body is None
            and commits is None
            and os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
        ):
            composition_notes.append(
                "Предупреждение: PARCH_PR_BODY не передан, раздел «Основания» не проверен: "
                "обновите шаблон CI проекта (шаг standard с env PARCH_PR_BODY)."
            )
        incidents_in_debt = managed
    workflows = sorted((project / ".github" / "workflows").glob("*.y*ml"))
    adr_text = accepted_adr_text(project)
    for path in workflows:
        problems.extend(standard_workflow_problems(path, adr_text))
    os_problem = target_os_problem(project)
    if os_problem:
        problems.append(os_problem)
    problems.extend(blocked_without_incident_problems(project))
    if not incidents_in_debt:
        problems.extend(incident_report_problems(project, block_ids_of(project)))
    problems.extend(incident_budget_problems(project))
    agents = project / "AGENTS.md"
    if agents.is_file():
        size = len(agents.read_text(encoding="utf-8").splitlines())
        if size > AGENTS_MAX_LINES:
            problems.append(
                f"AGENTS.md: {size} строк, бюджет {AGENTS_MAX_LINES}. Длинные инструкции модель "
                "выполняет хуже коротких: перенесите детали в docs/ и оставьте ссылки."
            )
    circle_files = sorted((project / ".circleci").glob("*.y*ml"))
    for path in circle_files:
        problems.extend(circleci_problems(path, adr_text))
    if problems:
        result.fail(f"Нарушения стандарта {STANDARD_VERSION} ({len(problems)}):", *shown(problems))
        return result
    result.note(
        f"Стандарт {STANDARD_VERSION}: проверено workflow GitHub {len(workflows)} (таймауты, "
        f"отмена, триггеры, раннеры, матрицы) и конфигов CircleCI {len(circle_files)} "
        "(расписание, исполнители, классы ресурсов, пределы времени), целевая ОС в "
        "CONSTITUTION.md и размер AGENTS.md."
    )
    if circle_files:
        result.note(
            "Автоотмена устаревших прогонов CircleCI (Project Settings, Advanced, "
            "Auto-cancel Redundant Workflows) из конфига не видна: включите её в проекте."
        )
    result.note(*composition_notes)
    result.note(f"Пока не проверяется: {STANDARD_NOT_YET}.")
    return result


def check_catalog(
    project: Path, language: str, update: bool = False, accept_new: bool = False
) -> Result:
    """Описания публичных функций и свежесть каталога docs/CAPABILITIES.md."""
    import parch_catalog  # лежит рядом (.github/parch/), в проект копируется вместе с этим файлом

    problems, notes = parch_catalog.check(project, update, accept_new)
    result = Result()
    if problems:
        result.fail(*problems)
    result.note(*notes)
    return result


def check_libraries(project: Path, language: str) -> Result:
    """Правило «библиотека в одном модуле» из state/architecture.json."""
    import parch_libraries  # лежит рядом (.github/parch/), в проект копируется вместе с этим файлом

    problems, notes = parch_libraries.check(project, language)
    result = Result()
    if problems:
        result.fail(*problems)
    result.note(*notes)
    return result


def check_basis(project: Path, language: str) -> Result:
    """«Основания» из коммитов ветки: печатается для описания PR (пишется один раз, в коммите)."""
    import parch_standard  # лежит рядом (.github/parch/), в проект копируется вместе с этим файлом

    base_ref = os.environ.get("PARCH_BASE_REF", "origin/main")
    messages, error = parch_standard.commit_messages(project, base_ref)
    problem = parch_standard.commits_basis_problem(messages, error)
    text = parch_standard.commits_basis_text(messages) if problem is None else None
    result = Result()
    if text is None:
        result.fail(problem or "в коммитах ветки нет раздела «Основания»")
    else:
        result.note(text.rstrip("\n"))
    return result


CHECKS: dict[str, Callable[[Path, str], Result]] = {
    "basis": check_basis,
    "tests": check_tests,
    "catalog": check_catalog,
    "libraries": check_libraries,
    "modules": check_modules,
    "deps": check_deps,
    "dead-code": check_dead_code,
    "duplicates": check_duplicates,
    "architecture": check_architecture,
    "coverage": check_coverage,
    "skips": check_skips,
    "suppressions": check_suppressions,
    "settings": check_settings,
    "thin": check_thin,
    "psscriptanalyzer": check_psscriptanalyzer,
    "standard": check_standard,
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
    parser.add_argument(
        "--partial",
        action="store_true",
        help="tests, skips: отчёт урезанного прогона (с пометкой parch-partial); слияния не даёт",
    )
    parser.add_argument(
        "--only-tests",
        action="store_true",
        help="baseline --update: только списки тестов и пропусков (остальное не трогать)",
    )
    parser.add_argument(
        "--report-platform",
        choices=["windows", "posix"],
        default=None,
        help="baseline --update: на какой системе снят отчёт (отчёт CI всегда posix)",
    )
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
            result = update_baseline(
                project, args.language, accept, report, args.only_tests, args.report_platform
            )
        elif args.check in COLLECTOR_CHECKS and args.language not in COLLECTORS:
            reason = (
                "не применяется (ADR-0010: PowerShell только тонкий клей)"
                if args.language == "powershell"
                else "ещё не реализована"
            )
            print(f"[parch:{args.check}] ПРОВАЛ: для языка {args.language} эта проверка {reason}")
            return 1
        elif args.check == "tests":
            result = check_tests(project, args.language, report, args.partial)
        elif args.check == "skips":
            result = check_skips(project, args.language, report, args.partial)
        elif args.check == "catalog":
            result = check_catalog(project, args.language, args.update, args.accept_new)
        elif args.check == "modules":
            result = check_modules(project, args.language, args.update, args.accept_new)
        elif args.check == "standard":
            result = check_standard(project, args.language, args.update, args.accept_new)
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
