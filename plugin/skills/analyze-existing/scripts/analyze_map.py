# ruff: noqa: E501
"""Карта модулей и швов между языками (docs/specs/F15-analyze-existing.md, PR 2).

Модули: папки с кодом по языкам (путь, назначение из описания, статус). Швы: места, где код одного языка запускает
код другого или где два языка читают и пишут один файл. **Шов без контрактного теста красный сигнал**: ошибка на стыке
(формат файла, аргументы запуска) не ловится ни одним тестом. Поиск приблизительный (по тексту, без разбора кода):
смысл теста проверяет агент, скрипт проверяет только, что тест упоминает шов.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Any

from analyze_deadcode import is_generated, is_test_path
from analyze_report import DRAFT_MARK, language_of

MODULE_MARKERS = (".csproj", "pyproject.toml", "package.json", "setup.py", ".sln")
ARCHIVE_NAMES = {"archive", "old", "legacy", "deprecated", "attic"}
MAX_FILE_BYTES = 1_500_000
MAX_MODULES = 60
MAX_SEAMS = 80
SHARED_EXT = r"csv|json|txt|yaml|yml|sqlite|db|parquet|xml|ini|cfg|toml|log"
SHARED_NAME = re.compile(rf"[\w.\-]+\.(?:{SHARED_EXT})\b", re.IGNORECASE)
STRING = re.compile(r"""@?"([^"\n]{3,200})"|'([^'\n]{3,200})'""")
NOISE_NAMES = {
    "package.json", "package-lock.json", "tsconfig.json", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "settings.json", "launch.json", "nuget.config", "global.json", "readme.txt",
    "license.txt", "tasks.json", "extensions.json", "appsettings.json",
}  # fmt: skip
LAUNCH_CALL = {
    "python": re.compile(r"subprocess\.|os\.system|os\.popen|Popen|os\.startfile|os\.spawn"),
    "csharp": re.compile(r"Process\.Start|ProcessStartInfo"),
    "powershell": re.compile(r"Start-Process|\bpython3?(?:\.exe)?\b\s|^\s*&\s|Invoke-Expression|\bdotnet\b|\bnode\b|\bpy\b\s", re.MULTILINE),
    "typescript": re.compile(r"child_process|\bspawn\(|\bexecSync\(|\bexec\(|\bexecFile\("),
}  # fmt: skip
TARGET_TOKENS = (
    ("powershell", re.compile(r"powershell|pwsh|\.ps1\b", re.IGNORECASE)),
    ("python", re.compile(r"\bpython3?(?:\.exe)?\b|sys\.executable|\.py\b|\bpy\b", re.IGNORECASE)),
    ("csharp", re.compile(r"\bdotnet\b|msbuild|\.csproj\b", re.IGNORECASE)),
    (
        "typescript",
        re.compile(
            r"\bnpx\b|\bts-node\b|\bnode(?:\.exe)?\b[\s\"',]+\S*\.(?:js|mjs|cjs|ts)\b",
            re.IGNORECASE,
        ),
    ),
)
SCRIPT_NAME = re.compile(r"[\w./\\\-]+\.(?:ps1|py|mjs|cjs)\b", re.IGNORECASE)
CODE_LANGUAGES = {"python", "csharp", "powershell", "typescript"}
LAUNCH_WORDS = re.compile(r"powershell|pwsh|subprocess|dotnet|node|python|Process", re.IGNORECASE)


def read(project: Path, path: str) -> str:
    file = project / path
    try:
        if file.stat().st_size > MAX_FILE_BYTES:
            return ""
        return file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def in_zone(path: str, zones: list[str]) -> bool:
    return any(path == z or path.startswith(z + "/") for z in zones)


# ---------- модули ----------


def module_root(path: str, markers: set[str]) -> str:
    """Папка модуля: ближайшая папка выше файла с маркером проекта (.csproj, pyproject…), иначе папка верхнего уровня."""
    parts = path.split("/")[:-1]
    for depth in range(len(parts), 0, -1):
        if "/".join(parts[:depth]) in markers:
            return "/".join(parts[:depth])
    return parts[0] if parts else "(корень)"


def purpose_of(project: Path, folder: str, rel_set: set[str]) -> str:
    """Назначение из описания: README папки, описание `__init__.py`, `<Description>` в `.csproj`; иначе вопрос."""
    base = "" if folder == "(корень)" else folder + "/"
    for name in ("README.md", "readme.md", "README.txt"):
        if base + name in rel_set:
            for line in read(project, base + name).splitlines():
                text = line.strip().lstrip("#").strip()
                if text and not text.startswith(("[", "!", "<", "---")):
                    return text[:140]
    if base + "__init__.py" in rel_set:
        try:
            doc = ast.get_docstring(ast.parse(read(project, base + "__init__.py")))
        except SyntaxError:
            doc = None
        if doc:
            return doc.strip().splitlines()[0][:140]
    for path in sorted(rel_set):
        if path.startswith(base) and path.endswith(".csproj") and "/" not in path[len(base) :]:
            found = re.search(r"<Description>([^<]+)</Description>", read(project, path))
            if found:
                return found.group(1).strip()[:140]
    return "описания нет: вопрос владельцу"


def modules(project: Path, rel: list[str], zones: list[str]) -> list[dict[str, Any]]:
    """Модули по папкам: путь, языки, файлы кода, файлы тестов, назначение, статус."""
    rel_set = set(rel)
    markers = {
        p.rsplit("/", 1)[0]
        for p in rel
        if "/" in p and p.rsplit("/", 1)[1].endswith(MODULE_MARKERS)
    }
    groups: dict[str, dict[str, Any]] = {}
    for path in rel:
        language = language_of(path)
        if not language or is_generated(path):
            continue
        folder = module_root(path, markers)
        group = groups.setdefault(folder, {"languages": Counter(), "files": 0, "test_files": 0})
        group["languages"][language] += 1
        group["files"] += 1
        group["test_files"] += int(is_test_path(path))
    rows: list[dict[str, Any]] = []
    for folder, group in groups.items():
        if folder in ARCHIVE_NAMES or folder.split("/")[0] in ARCHIVE_NAMES:
            status = "архив"
        elif in_zone(folder, zones):
            status = "неприкосновенная зона"
        else:
            status = "рабочий"
        rows.append(
            {
                "path": folder,
                "languages": dict(group["languages"]),
                "files": group["files"],
                "test_files": group["test_files"],
                "purpose": purpose_of(project, folder, rel_set),
                "status": status,
            }
        )
    rows.sort(key=lambda r: (-r["files"], r["path"]))
    return rows[:MAX_MODULES]


# ---------- швы ----------


def window(lines: list[str], index: int, size: int = 4) -> str:
    return " ".join(lines[index : index + size])


def launch_seams(project: Path, files: list[str]) -> list[dict[str, Any]]:
    """Швы запуска: код одного языка запускает программу или скрипт другого языка проекта."""
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in files:
        source = language_of(path)
        pattern = LAUNCH_CALL.get(source)
        if pattern is None:
            continue
        lines = read(project, path).splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith(("#", "//")) or not pattern.search(line):
                continue
            text = window(lines, index)
            for target, token in TARGET_TOKENS:
                if target == source or not token.search(text):
                    continue
                key = (path, source, target)
                seam = found.setdefault(
                    key,
                    {
                        "kind": "запуск",
                        "from": source,
                        "to": target,
                        "files": [path],
                        "evidence": [],
                        "keys": set(),
                    },
                )
                if len(seam["evidence"]) < 3:
                    seam["evidence"].append(f"{path}:{index + 1}")
                for script in SCRIPT_NAME.findall(text):
                    seam["keys"].add(Path(script.replace(chr(92), "/")).name)
                    for other in files:
                        if (
                            other.endswith("/" + Path(script.replace(chr(92), "/")).name)
                            or other == script
                        ):
                            if other not in seam["files"]:
                                seam["files"].append(other)
    return list(found.values())


def shared_file_seams(project: Path, files: list[str]) -> list[dict[str, Any]]:
    """Швы по общему файлу: одно имя файла данных названо в коде двух и более языков."""
    by_name: dict[str, dict[str, list[str]]] = {}
    for path in files:
        language = language_of(path)
        text = read(project, path)
        for number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith(("#", "//")):
                continue
            for match in STRING.finditer(line):
                literal = match.group(1) or match.group(2) or ""
                for name in SHARED_NAME.findall(literal):
                    base = Path(literal.replace(chr(92), "/")).name.lower()
                    if not SHARED_NAME.fullmatch(base):
                        base = name.lower()
                    if base in NOISE_NAMES or base.startswith("."):
                        continue
                    by_name.setdefault(base, {}).setdefault(language, []).append(f"{path}:{number}")
    seams: list[dict[str, Any]] = []
    for base, languages in by_name.items():
        if len(languages) < 2:
            continue
        ordered = sorted(languages)
        seams.append(
            {
                "kind": "общий файл",
                "from": ordered[0],
                "to": ", ".join(ordered[1:]),
                "files": sorted({e.rsplit(":", 1)[0] for v in languages.values() for e in v}),
                "evidence": [v[0] for _, v in sorted(languages.items())][:3],
                "keys": {base},
            }
        )
    return seams


def contract_tests(seam: dict[str, Any], tests: dict[str, str]) -> list[str]:
    """Тесты, которые называют шов: имя общего файла или скрипта; для запуска без имени нужен ещё признак запуска."""
    keys = {str(k).lower() for k in seam["keys"]}
    named = bool(keys)
    if not named:
        keys = {Path(f).stem.lower() for f in seam["files"][:1]}
    found: list[str] = []
    for path, text in tests.items():
        if path in seam["files"]:
            continue
        lowered = text.lower()
        if any(k in lowered for k in keys) and (named or LAUNCH_WORDS.search(text)):
            found.append(path)
    return sorted(found)[:5]


def seams(project: Path, rel: list[str]) -> list[dict[str, Any]]:
    """Швы между языками с пометкой «красный», если контрактного теста нет."""
    code = [p for p in rel if language_of(p) in CODE_LANGUAGES and not is_generated(p)]
    own = [p for p in code if not is_test_path(p)]
    tests = {p: read(project, p) for p in code if is_test_path(p)}
    found = launch_seams(project, own) + shared_file_seams(project, own)
    for number, seam in enumerate(sorted(found, key=lambda s: (s["kind"], s["files"][0])), start=1):
        seam["id"] = f"S{number}"
        seam["keys"] = sorted(seam["keys"])
        seam["contract_tests"] = contract_tests(seam, tests)
        seam["red"] = not seam["contract_tests"]
    found.sort(key=lambda s: (not s["red"], s["id"]))
    return found[:MAX_SEAMS]


def by_language(found: list[dict[str, Any]], language: str) -> tuple[int, int]:
    """(швов с участием языка, из них без контрактного теста)."""
    mine = [s for s in found if language == s["from"] or language in str(s["to"]).split(", ")]
    return len(mine), sum(1 for s in mine if s["red"])


# ---------- черновики ----------


def modules_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# MODULES — карта модулей ({DRAFT_MARK})",
        "",
        "Модуль — папка с кодом (или проект с `.csproj`, `pyproject.toml`, `package.json`). Назначение взято из описания; "
        "где его нет, это вопрос владельцу. Статус «неприкосновенная зона» значит, что код нельзя менять без отдельного «да».",
        "",
        "| Путь | Языки | Файлов кода | Из них тестов | Назначение | Статус |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        langs = ", ".join(f"{k}: {v}" for k, v in sorted(r["languages"].items()))
        lines.append(
            f"| {r['path']} | {langs} | {r['files']} | {r['test_files']} | {r['purpose']} | {r['status']} |"
        )
    if not rows:
        lines.append("| | | | | Кода не найдено | |")
    return chr(10).join(lines) + chr(10)


def interfaces_markdown(found: list[dict[str, Any]]) -> str:
    red = sum(1 for s in found if s["red"])
    lines = [
        f"# INTERFACES — швы между языками ({DRAFT_MARK})",
        "",
        "Шов — место, где код одного языка запускает код другого или два языка читают и пишут один файл. "
        "**Шов без контрактного теста — красный сигнал**: ошибку на стыке не поймает ни один тест. Менять шов можно "
        "только после контрактного теста этого шва. Поиск приблизительный (по тексту): смысл теста проверяет агент.",
        "",
        f"Швов: {len(found)}, без контрактного теста: {red}.",
        "",
        "| № | Вид | От → к | Где | Контрактный тест |",
        "|---|---|---|---|---|",
    ]
    for s in found:
        where = ", ".join(f"`{e}`" for e in s["evidence"])
        tests = (
            ", ".join(f"`{t}`" for t in s["contract_tests"])
            if s["contract_tests"]
            else "**нет (красный)**"
        )
        lines.append(
            f"| {s['id']} | {s['kind']}: {', '.join(s['keys']) or 'запуск процесса'} | {s['from']} → {s['to']} | {where} | {tests} |"
        )
    if not found:
        lines.append("| | | Швов между языками не найдено | | |")
    return chr(10).join(lines) + chr(10)
