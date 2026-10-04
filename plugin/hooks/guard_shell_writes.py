# ruff: noqa: E501
"""PreToolUse: запись файлов проекта через командную строку запрещена, только Write и Edit.

Причина (docs/LESSONS.md): запись через heredoc и `echo >` портит строки (обратные слэши и переводы строк),
а правка в обход инструментов Write/Edit не видна проверкам после правки и журналу. Запрещено:
перенаправление `>` и `>>` в файл, `tee`, `sed -i`, `Set-Content`, `Add-Content`, `Out-File` и скрипт из
heredoc или `-c`, который пишет файл. Разрешено: отчёты тестов (`test-report.xml`, junit), покрытие
(`.coverage`, `coverage.xml`, `htmlcov/`) и временные файлы вне проекта (системная временная папка, `$TEMP`,
`/tmp`). Работает в подключённых проектах и в репозитории самого продукта.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from _common import (
    SHELL_TOOLS,
    Block,
    JsonDict,
    command_name,
    command_tokens,
    get_str,
    is_managed,
    run_guard,
    shell_command,
)
from pre_push import is_product_repo

HOOK = "guard_shell_writes"
HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(?P<tag>\w+)\1(?P<rest>[^\n]*)\n(?P<body>.*?)(?:\n\s*(?P=tag)\s*(?:\n|$)|$)",
    re.DOTALL,
)
INLINE_SCRIPT = re.compile(
    r"(?:^|\s)(?:python3?|py|node|pwsh|powershell)\b[^\n]*?\s(?:-c|-e|-Command)\s+(?P<body>.+)",
    re.DOTALL | re.IGNORECASE,
)
WRITE_PATTERN = re.compile(
    r"write_text\(|write_bytes\(|\.write\(|open\([^)]*['\"][wax]\+?b?['\"]|json\.dump\(|"
    r"shutil\.(?:copy|move)|os\.(?:replace|rename)\(|Set-Content|Add-Content|Out-File|"
    r"WriteAllText|WriteAllLines|\.to_csv\(|\.save\(",
    re.IGNORECASE,
)
ALLOWED_NAMES = {
    "test-report.xml", "test-report-partial.xml", ".coverage", "coverage.xml", "junit.xml",
    "nul", "null", "$null",
}  # fmt: skip
ALLOWED_MARKERS = (
    "test-report", ".coverage", "coverage.xml", "htmlcov", "junit", "tempfile", "gettempdir",
    "$TEMP", "${TEMP}", "%TEMP%", "$env:TEMP", "$env:TMP", "$TMPDIR", "$RUNNER_TEMP", "/tmp/",
)  # fmt: skip
TEMP_PREFIXES = (
    "$TEMP", "${TEMP}", "%TEMP%", "$env:TEMP", "$env:TMP", "$TMPDIR", "$RUNNER_TEMP", "/tmp/",
)  # fmt: skip
WRITERS = {"tee", "set-content", "add-content", "out-file"}


def split_heredocs(command: str) -> tuple[str, str]:
    """(команда без тел heredoc, тела heredoc). Тела не разбираются как команды."""
    bodies = [m["body"] for m in HEREDOC.finditer(command)]
    return HEREDOC.sub(lambda m: m["rest"], command), chr(10).join(bodies)


def redirect_targets(tokens: list[str]) -> list[str]:
    targets: list[str] = []
    for index, token in enumerate(tokens):
        match = re.match(r"^(?:\d*>>?|&>>?)(?P<rest>.*)$", token)
        if not match or match["rest"].startswith("&"):
            continue
        rest = match["rest"]
        if rest:
            targets.append(rest)
        elif index + 1 < len(tokens):
            targets.append(tokens[index + 1])
    return targets


def sed_in_place(tokens: list[str]) -> bool:
    return command_name(tokens) in {"sed", "perl"} and any(
        t.startswith("-i") or re.fullmatch(r"-[a-zA-Z]+i[a-zA-Z]*", t) for t in tokens[1:]
    )


def target_allowed(target: str, project: Path, command: str) -> bool:
    """Отчёты тестов, покрытие и временные файлы вне проекта."""
    cleaned = target.strip("\"'")
    parts = cleaned.replace(chr(92), "/").split("/")
    if parts[-1].lower() in ALLOWED_NAMES or "htmlcov" in parts:
        return True
    if cleaned.startswith(TEMP_PREFIXES):
        # переменную временной папки не должна подменять сама команда (TEMP=проект ... > $TEMP/x)
        return not re.search(r"\b(?:TEMP|TMP|TMPDIR)=", command)
    if not Path(cleaned).is_absolute():
        return False
    try:
        path = Path(cleaned).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        root = project.resolve()
    except OSError:
        return False
    outside_project = path != root and root not in path.parents and path not in root.parents
    return temp in path.parents and outside_project


def writes(command: str, project: Path) -> str | None:
    """Что именно пишет файл проекта (None, если команда безопасна)."""
    outer, bodies = split_heredocs(command)
    for tokens in command_tokens(outer):
        for target in redirect_targets(tokens):
            if not target_allowed(target, project, command):
                return f"перенаправление в файл {target}"
        name = command_name(tokens)
        if sed_in_place(tokens):
            return f"{name} -i правит файл на месте"
        if name in WRITERS:
            files = [t for t in tokens[1:] if not t.startswith("-")]
            if not files or not all(target_allowed(f, project, command) for f in files):
                return f"{name} пишет файл"
    inline = INLINE_SCRIPT.search(outer)
    script = bodies + (chr(10) + inline["body"] if inline else "")
    if script.strip() and WRITE_PATTERN.search(script):
        if not any(marker in command for marker in ALLOWED_MARKERS):
            return "скрипт из heredoc или -c пишет файл"
    return None


def check(data: JsonDict, project: Path) -> Block | None:
    if get_str(data, "tool_name") not in SHELL_TOOLS:
        return None
    if not (is_managed(project) or is_product_repo(project)):
        return None
    what = writes(shell_command(data), project)
    if what is None:
        return None
    return Block(
        HOOK,
        f"Запись файлов проекта через командную строку запрещена ({what}): используй инструмент "
        "Write или Edit, чтобы правка была видна проверкам после правки и журналу, а строки не "
        "портились (экранирование в heredoc). Разрешены отчёты тестов, покрытие и временные файлы "
        "вне проекта. Скрипт для работы с данными пиши файлом через Write и запускай командой.",
    )


if __name__ == "__main__":
    run_guard(HOOK, check)
