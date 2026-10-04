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
FORMAT_CHECK = re.compile(
    r"header|column|fieldnames|DictReader|csv\.reader|json\.loads?|JsonSerializer|JsonDocument|schema|\.keys\(\)"
    r"|returncode|exit[ _]?code|ExitCode|LASTEXITCODE|splitlines|ReadAllLines|startswith|\bkeys?\b|\bfields?\b",
    re.IGNORECASE,
)
ASSERTION = re.compile(r"assert|Assert|Should|Expect|self\.fail|raise AssertionError")
# Запись за пределы проекта: в файле есть путь вне репозитория (диск, папка игры, BepInEx, Steam) и операция записи.
OUTSIDE_PATH = re.compile(
    r"(?<![\w/])[A-Za-z]:[\\/]|program files|steamapps|bepinex|gamedir|%appdata%|\$env:(?:appdata|localappdata|programfiles|userprofile)|~[\\/]|(?<![\w.])/(?:etc|usr|var|opt)/",
    re.IGNORECASE,
)
WRITE_OPS = re.compile(
    r"Copy-Item|Move-Item|Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Rename-Item|Expand-Archive"
    r"|Register-ScheduledTask|schtasks\s+/create|shutil\.(?:copy\w*|move|rmtree)|os\.(?:remove|rename|replace|makedirs)"
    r"|\.write_text|\.write_bytes|File\.(?:Copy|Move|Delete|WriteAll\w+|Create)|Directory\.(?:CreateDirectory|Delete|Move)"
    r"|fs\.(?:write\w*|copy\w*|rm\w*|unlink|mkdir|rename)"
)
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


DOCSTRING = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')


def executable_text(text: str) -> str:
    """Текст теста без комментариев и docstring: имя в пояснении не значит, что тест трогает этот код."""
    lines = [
        ln
        for ln in DOCSTRING.sub("", text).splitlines()
        if not ln.lstrip().startswith(("#", "//", "*", "/*"))
    ]
    return chr(10).join(lines)


def mentions(text: str, name: str) -> bool:
    """Имя встречается в тексте как отдельное слово (без учёта регистра); слишком короткие имена не считаются."""
    return len(name) >= 3 and bool(
        re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE)
    )


def side_groups(seam: dict[str, Any]) -> list[set[str]]:
    """Стороны шва: для каждого языка набор имён его файлов (имя файла и имя без расширения)."""
    groups: dict[str, set[str]] = {}
    own_names = {
        Path(str(k)).stem.lower() for k in seam["keys"]
    }  # имя самого общего файла стороной не считается
    for path in seam["files"]:
        name = Path(path).name
        names = {name.lower(), Path(path).stem.lower()} - own_names
        groups.setdefault(language_of(path), set()).update(names)
    return list(groups.values())


def covers_both_sides(seam: dict[str, Any], text: str) -> bool:
    """Тест называет код каждой стороны шва; у запуска без скрипта проекта сторона-цель определяется по слову запуска."""
    if not all(any(mentions(text, n) for n in names) for names in side_groups(seam)):
        return False
    if seam["kind"] == "запуск":
        target = next(t for lang, t in TARGET_TOKENS if lang == seam["to"])
        keys = [str(k) for k in seam["keys"]]
        return any(mentions(text, k) for k in keys) if keys else bool(target.search(text))
    return any(mentions(text, str(k)) for k in seam["keys"])


ASSIGNED = re.compile(r"(?:\$|(?<![\w.]))([A-Za-z_]\w{2,})\s*=(?!=)")


COPY_OP = re.compile(r"Copy-Item|Move-Item|shutil\.(?:copy\w*|move)|File\.(?:Copy|Move)")
FIRST_ARG = re.compile(r"^\W*[^\s,)]+[\s,]+")


def outside_part(line: str) -> str:
    """Часть строки, куда идёт запись: у копирования и переноса это приёмник (второй аргумент), источник только читается."""
    op = WRITE_OPS.search(line)
    if op and COPY_OP.fullmatch(op.group(0)):
        rest = line[op.end() :]
        first = FIRST_ARG.match(rest)
        return rest[first.end() :] if first else rest
    return line


def without_root(line: str, roots: list[str]) -> str:
    """Строка без абсолютных путей самого проекта: путь внутрь своего репозитория не «вне проекта»."""
    for root in roots:
        line = re.sub(re.escape(root), "", line, flags=re.IGNORECASE)
    return line


def tainted_names(lines: list[str], roots: list[str]) -> dict[str, int]:
    """Переменные, в которые попал путь вне проекта (прямо или через другую такую переменную): {имя: номер строки}."""
    names: dict[str, int] = {}
    for _ in range(3):  # цепочка присваиваний: $GameDir -> $target -> ...
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith(("#", "//")):
                continue
            for match in ASSIGNED.finditer(line):
                name = match.group(1)
                rest = without_root(line[match.end() :], roots)
                if name not in names and (
                    OUTSIDE_PATH.search(rest) or any(uses(rest, other) for other in names)
                ):
                    names[name] = number
    return names


def uses(text: str, name: str) -> bool:
    """Переменная названа в тексте: `$имя` или слово не внутри строки, не после точки (так `.log` не принимается за `log`)."""
    escaped = re.escape(name)
    return bool(re.search(rf"\${escaped}(?!\w)|(?<![\w.$\"'/\\]){escaped}(?![\w\"'])", text))


def outside_writers(project: Path, files: list[str]) -> dict[str, list[str]]:
    """Файлы, которые по тексту пишут за пределы проекта: {путь: [строка с путём, строка с операцией записи]}.

    Запись внешняя, если в строке с операцией записи есть путь вне репозитория (диск, папка игры, BepInEx, Steam)
    или переменная, в которую такой путь попал раньше. Просто упоминание папки игры без записи в неё не считается.
    """
    found: dict[str, list[str]] = {}
    root = str(project.resolve())
    roots = [root, root.replace(chr(92), "/"), root.replace(chr(92), chr(92) * 2)]
    for path in files:
        lines = read(project, path).splitlines()
        names = tainted_names(lines, roots)
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith(("#", "//")) or not WRITE_OPS.search(line):
                continue
            target = without_root(outside_part(line), roots)
            direct = OUTSIDE_PATH.search(target) is not None
            via = next((n for n in names if uses(target, n)), None)
            if direct or via:
                origin = number if direct else names[via or ""]
                found[path] = [f"{path}:{origin}", f"{path}:{number}"]
                break
    return found


def contract_tests(seam: dict[str, Any], tests: dict[str, str]) -> tuple[list[str], list[str]]:
    """(контрактные тесты, тесты, которые только называют шов).

    Контрактным считается тест, который называет код обеих сторон шва (запускает или читает обе) и сверяет формат
    (колонки, поля, код возврата). Тест, который только называет файл или скрипт, не засчитывается.
    """
    keys = [str(k) for k in seam["keys"]] or [Path(f).stem for f in seam["files"][:1]]
    strong: list[str] = []
    weak: list[str] = []
    for path, text in tests.items():
        if path in seam["files"] or not any(mentions(text, k) for k in keys):
            continue
        if covers_both_sides(seam, text) and FORMAT_CHECK.search(text) and ASSERTION.search(text):
            strong.append(path)
        elif seam["keys"] or LAUNCH_WORDS.search(text):  # без имени шва нужен ещё признак запуска
            weak.append(path)
    return sorted(strong)[:5], sorted(weak)[:5]


def seams(project: Path, rel: list[str]) -> list[dict[str, Any]]:
    """Швы между языками с пометкой «красный», если контрактного теста нет."""
    code = [p for p in rel if language_of(p) in CODE_LANGUAGES and not is_generated(p)]
    own = [p for p in code if not is_test_path(p)]
    tests = {p: executable_text(read(project, p)) for p in code if is_test_path(p)}
    outside = outside_writers(project, own)
    found = launch_seams(project, own) + shared_file_seams(project, own)
    for number, seam in enumerate(sorted(found, key=lambda s: (s["kind"], s["files"][0])), start=1):
        seam["id"] = f"S{number}"
        seam["keys"] = sorted(seam["keys"])
        seam["contract_tests"], seam["weak_tests"] = contract_tests(seam, tests)
        seam["red"] = not seam["contract_tests"]
        seam["outside_write"] = [f for f in seam["files"] if f in outside]
    found.sort(key=lambda s: (not s["red"], s["id"]))
    return found[:MAX_SEAMS]


def outside_writes(project: Path, rel: list[str]) -> dict[str, list[str]]:
    """Файлы проекта, которые по тексту пишут за пределы репозитория (папка игры, BepInEx, Steam, другие диски)."""
    code = [
        p
        for p in rel
        if language_of(p) in CODE_LANGUAGES and not is_generated(p) and not is_test_path(p)
    ]
    return outside_writers(project, code)


def by_language(found: list[dict[str, Any]], language: str) -> tuple[int, int]:
    """(швов с участием языка, из них без контрактного теста)."""
    mine = [s for s in found if language == s["from"] or language in str(s["to"]).split(", ")]
    return len(mine), sum(1 for s in mine if s["red"])


# ---------- черновики ----------


ZONE_NOTE = "неприкосновенная зона, нужно отдельное «да»"


def modules_markdown(
    rows: list[dict[str, Any]], outside: dict[str, list[str]] | None = None
) -> str:
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
    if outside:
        lines += [
            "",
            "## Файлы, которые пишут за пределы проекта",
            "",
            f"По тексту эти файлы пишут в папки вне репозитория (папка игры, BepInEx, Steam, другие диски): {ZONE_NOTE}. "
            "Поиск приблизительный: сначала агент читает файл и подтверждает или снимает пометку.",
            "",
            "| Файл | Где путь вне проекта | Где запись | Пометка |",
            "|---|---|---|---|",
        ]
        for path, places in sorted(outside.items()):
            lines.append(f"| {path} | `{places[0]}` | `{places[1]}` | {ZONE_NOTE} |")
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
        "Контрактным считается только тест, который называет код обеих сторон шва (запускает или читает обе) и сверяет "
        "формат (колонки, поля, код возврата). Тест, который только называет файл или скрипт, не засчитывается: он "
        "показан отдельно, шов остаётся красным. Два теста, каждый про свою сторону, тоже не засчитываются.",
        "",
        "| № | Вид | От → к | Где | Контрактный тест | Пишет вне проекта |",
        "|---|---|---|---|---|---|",
    ]
    for s in found:
        where = ", ".join(f"`{e}`" for e in s["evidence"])
        tests = (
            ", ".join(f"`{t}`" for t in s["contract_tests"])
            if s["contract_tests"]
            else "**нет (красный)**"
        )
        if s["weak_tests"] and not s["contract_tests"]:
            tests += (
                " (только называют шов, не засчитаны: "
                + ", ".join(f"`{t}`" for t in s["weak_tests"])
                + ")"
            )
        outside = ", ".join(f"`{f}`: {ZONE_NOTE}" for f in s["outside_write"]) or "—"
        lines.append(
            f"| {s['id']} | {s['kind']}: {', '.join(s['keys']) or 'запуск процесса'} | {s['from']} → {s['to']} | {where} | {tests} | {outside} |"
        )
    if not found:
        lines.append("| | | Швов между языками не найдено | | | |")
    return chr(10).join(lines) + chr(10)
