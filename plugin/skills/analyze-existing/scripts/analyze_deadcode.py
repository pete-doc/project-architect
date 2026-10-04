# ruff: noqa: E501
"""Кандидаты в мёртвый код и вопросы владельцу (docs/design/analyze-existing.md, «Что анализ не делает и почему»).

Анализ ничего не удаляет: найденное попадает в QUESTIONS.md простыми вопросами по файлам. Поиск приблизительный: имя,
которое в проекте встречается один раз (в самом объявлении), считается кандидатом. Обработчики фреймворков (`do_GET`,
`Awake`, `Postfix`), методы с атрибутами и сгенерированные файлы отсеиваются: вызова в коде у них нет, но они живые.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {
    ".py", ".cs", ".ps1", ".psm1", ".psd1", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml", ".md", ".csproj",
    ".props", ".sh", ".cmd", ".bat", ".toml", ".cfg", ".ini", ".xml",
}  # fmt: skip
MAX_FILE_BYTES = 1_500_000
MAX_CANDIDATES = 50
SKIP_NAMES = {"main", "Main", "setup", "teardown", "setUp", "tearDown"}
# Имена, которые вызывает сам фреймворк, а не код проекта: по счёту вхождений они всегда «одинокие», но не мёртвые.
FRAMEWORK_NAMES = {
    # http.server, unittest, ast, logging, threading
    "log_message", "log_request", "log_error", "handle", "handle_one_request", "setUpClass",
    "tearDownClass", "asyncSetUp", "asyncTearDown", "default", "emit", "format", "run", "close",
    # Unity и BepInEx
    "Awake", "Start", "Update", "FixedUpdate", "LateUpdate", "OnEnable", "OnDisable", "OnDestroy",
    "OnGUI", "OnApplicationQuit", "Load", "Unload", "Initialize", "Finalize",
    # Harmony-патчи находятся по имени, а не по вызову
    "Prefix", "Postfix", "Transpiler", "Finalizer", "Patch",
    # .NET
    "Dispose", "ToString", "Equals", "GetHashCode", "Execute", "Invoke", "Configure", "ConfigureServices",
}  # fmt: skip
FRAMEWORK_PATTERNS = re.compile(r"^(do_[A-Z]+|visit_\w+|on_\w+|On[A-Z]\w+|handle_\w+)$")
ATTRIBUTE_LINE = re.compile(
    r"^\s*\[[A-Za-z_][\w.]*(\(.*\))?(\s*,\s*[A-Za-z_][\w.]*(\(.*\))?)*\]\s*$"
)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PS_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)+")
CS_DECL = re.compile(
    r"^\s*(?:public|internal)\s+(?:static\s+|sealed\s+|abstract\s+|partial\s+|readonly\s+)*"
    r"(?:class|record|struct|interface|enum)\s+(\w+)",
    re.MULTILINE,
)
CS_METHOD = re.compile(
    r"^\s*(?:public|internal)\s+(?:static\s+|async\s+|virtual\s+)*[\w<>\[\],.?]+\s+(\w+)\s*\(",
    re.MULTILINE,
)
PS_FUNCTION = re.compile(r"^\s*function\s+([A-Za-z][\w-]*)", re.MULTILINE | re.IGNORECASE)


def is_generated(path: str) -> bool:
    """Сгенерированный или декомпилированный код: кандидатов в нём не ищем."""
    lowered = path.lower()
    return "decompiled" in lowered or lowered.endswith((".g.cs", ".designer.cs"))


def is_framework_name(name: str) -> bool:
    """Обработчик, который зовёт фреймворк по имени (do_GET, Awake, Postfix…): вызова в коде проекта нет и не будет."""
    return name in FRAMEWORK_NAMES or bool(FRAMEWORK_PATTERNS.match(name))


def has_attribute(text: str, line: int) -> bool:
    """Над объявлением C# стоит атрибут (например [HarmonyPatch], [Fact]): его читает фреймворк, а не вызов."""
    lines = text.splitlines()
    index = line - 2
    while index >= 0 and not lines[index].strip():
        index -= 1
    return index >= 0 and bool(ATTRIBUTE_LINE.match(lines[index]))


def is_test_path(path: str) -> bool:
    return bool(
        re.search(
            r"(^|/)(tests?/|test_|[^/]*Tests?\.cs$|[^/]*\.Tests?\.ps1$|[^/]*\.(test|spec)\.)", path
        )
    )


def identifier_counts(project: Path, rel: list[str]) -> Counter[str]:
    """Сколько раз каждое имя встречается во всех текстах проекта (код, настройки, документы)."""
    counts: Counter[str] = Counter()
    for path in rel:
        if Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        file = project / path
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        counts.update(IDENT.findall(text))
        counts.update(PS_NAME.findall(text))
    return counts


def python_definitions(path: str, text: str) -> list[tuple[str, str, int]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.decorator_list
        ):
            kind = "класс" if isinstance(node, ast.ClassDef) else "функция"
            found.append((node.name, kind, node.lineno))
    return found


def declarations(path: str, text: str) -> list[tuple[str, str, int]]:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return python_definitions(path, text)
    found: list[tuple[str, str, int]] = []
    if suffix == ".cs":
        for pattern, kind in ((CS_DECL, "тип"), (CS_METHOD, "метод")):
            for match in pattern.finditer(text):
                found.append((match[1], kind, text.count(chr(10), 0, match.start()) + 1))
    elif suffix in {".ps1", ".psm1"}:
        for match in PS_FUNCTION.finditer(text):
            found.append(
                (match[1], "функция PowerShell", text.count(chr(10), 0, match.start()) + 1)
            )
    return found


def candidates(project: Path, rel: list[str]) -> dict[str, Any]:
    """Публичные имена, которые в проекте встречаются один раз (только в объявлении)."""
    counts = identifier_counts(project, rel)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in rel:
        if (
            is_test_path(path)
            or is_generated(path)
            or Path(path).suffix.lower() not in {".py", ".cs", ".ps1", ".psm1"}
        ):
            continue
        try:
            text = (project / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, kind, line in declarations(path, text):
            if (
                name.startswith("_")
                or name in SKIP_NAMES
                or len(name) < 3
                or name.startswith(("test", "Test"))
                or is_framework_name(name)
                or (path.endswith(".cs") and has_attribute(text, line))
            ):
                continue
            if counts[name] <= 1 and (path, name) not in seen:
                seen.add((path, name))
                rows.append({"name": name, "kind": kind, "path": path, "line": line})
    rows.sort(key=lambda r: (r["path"], r["line"]))
    names = {".py": "python", ".cs": "csharp", ".ps1": "powershell", ".psm1": "powershell"}
    by_language = Counter(names[Path(r["path"]).suffix.lower()] for r in rows)
    return {"total": len(rows), "shown": rows[:MAX_CANDIDATES], "by_language": dict(by_language)}


def questions_markdown(base: list[dict[str, str]], dead: dict[str, Any]) -> str:
    """QUESTIONS.md для parch-analysis/: вопросы карточки и вопросы по мёртвому коду."""
    lines = [
        "# Вопросы владельцу (черновик анализа)",
        "",
        "Ответы пишет владелец; анализ ничего не угадывает и ничего не удаляет.",
        "",
        "## Вопросы по процессу",
        "",
        "| № | Вопрос | Ответ владельца |",
        "|---|---|---|",
    ]
    for item in base:
        lines.append(f"| {item['id']} | {item['question']} | |")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in dead["shown"]:
        groups.setdefault(str(row["path"]), []).append(row)
    lines += [
        "",
        "## Возможно ненужный код",
        "",
        f"Нашлось имён, которые нигде больше не встречаются: {dead['total']} (показано {len(dead['shown'])}). "
        "Обработчики фреймворков (`do_GET`, `Awake`, `Postfix` и т. п.), код с атрибутами и сгенерированные файлы "
        "отсеяны заранее и сюда не попали. Один вопрос на файл. Ответ достаточно одной буквой: "
        "**А** — это нужно (запускают снаружи: вручную, по расписанию, другой программой или игрой); "
        "**Б** — это старое, можно убрать после проверки; **В** — не знаю.",
        "",
        "| Файл | Имена, которых нигде больше нет | Вопрос | Ответ (А / Б / В) |",
        "|---|---|---|---|",
    ]
    for path, rows in groups.items():
        names = ", ".join(f"`{r['name']}`" for r in rows[:8]) + (
            f" и ещё {len(rows) - 8}" if len(rows) > 8 else ""
        )
        lines.append(f"| {path} | {names} | {file_question(path)} | |")
    return chr(10).join(lines) + chr(10)


def file_question(path: str) -> str:
    """Простой вопрос про файл: только то, что знает владелец (кто и как запускает), а не то, что видно в коде."""
    if path.lower().endswith((".ps1", ".psm1")):
        return "Этот скрипт вы запускаете сами или он запускается по расписанию?"
    if path.lower().endswith(".cs"):
        return "Это нужно игре или плагину, который эту часть вызывает по имени, или осталось от старого?"
    return "Эти функции вы запускаете из командной строки или другой программой, или они остались от старого?"
