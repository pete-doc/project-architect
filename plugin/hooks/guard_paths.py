"""PreToolUse: охрана путей. Правила одинаковы для всех, включая основную сессию без роли.

1. Тесты: правят только роли architect и tester (список TEST_ROLES). Остальным блок. Роль
   tester, наоборот, правит только тесты и docs/QUESTIONS.md (TESTER_EXTRA_PATHS): остальное блок.
2. Защищённые пути (настройки Claude и hooks, CI-файлы, файлы state/ проверок, CONSTITUTION.md,
   docs/GOAL.md, принятые ADR): блокировка для всех. Снять её может только владелец,
   подтвердив запрос, который Claude Code показывает ему (решение «ask»). Агент подтвердить
   запрос не может.
   Когда запрос показать некому (режимы bypassPermissions и dontAsk, фоновый запуск), действие
   запрещается совсем.
3. Любая роль не создаёт новые .md вне docs/ (кроме короткого списка исключений; в репозитории
   продукта ещё `plugin/templates/`, ADR-0016).

Активен только в проектах с CONSTITUTION.md. Правки через Bash/PowerShell ловятся
приблизительно; жёсткая защита: правила permissions (plugin/plugin/templates/claude/settings.json).
"""

from __future__ import annotations

import fnmatch
import importlib
import re
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import cast

from _common import (
    CD_COMMANDS,
    FILE_TOOLS,
    SHELL_TOOLS,
    Ask,
    Block,
    JsonDict,
    agent_role,
    command_name,
    command_tokens,
    get_str,
    is_managed,
    rel_posix,
    roots_for_path,
    run_guard,
    shell_command,
    target_paths,
    tool_input,
)
from guard_shell_writes import TEMP_PREFIXES, target_allowed

HOOK = "guard_paths"
TEST_ROLES = frozenset({"architect", "tester"})
TESTER_EXTRA_PATHS = frozenset(
    {"docs/questions.md"}
)  # сюда tester пишет вопросы по описанию блока (в нижнем регистре)

TESTER_TEST_DIRS = frozenset({"tests", "test", "__tests__"})
TESTER_CS_SUFFIXES = (".cs", ".csproj", ".json", ".resx")
TESTER_CACHE_DIRS = frozenset(
    {".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache", "testresults"}
)
# файлы вне тестовой папки, которые tester вправе писать (имена в нижнем регистре)
TESTER_NAME_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.*",
    "*.spec.*",
    "*.tests.cs",
    "*.tests.ps1",
)

_TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
_TEST_NAME_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "*.test.*",
    "*.spec.*",
    "*test.cs",
    "*tests.cs",
    "*.tests.ps1",
)
_CI_ROOT_FILES = {
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "jenkinsfile",
    ".pre-commit-config.yaml",
}
_CI_DIRS = {".github", ".circleci"}
_STATE_FILES = {
    "features.json",
    "baseline.json",
    "jscpd-baseline.json",
    "standard-baseline.json",
    "catalog-baseline.json",
    "modules-baseline.json",
    "vulture-whitelist.py",
}
_ALLOWED_ROOT_MD = {"agents.md", "claude.md", "readme.md"}
_ADR_STATUS = re.compile(r"(?im)^\s*[-*]?\s*(?:статус|status)\s*:\s*(accepted|принят|утвержд)")

_MUTATING_COMMANDS = {
    "rm",
    "mv",
    "touch",
    "tee",
    "truncate",
    "del",
    "erase",
    "ri",
    "remove-item",
    "move",
    "move-item",
    "mi",
    "rename-item",
    "ren",
    "set-content",
    "add-content",
    "out-file",
    "clear-content",
    "new-item",
    "ni",
    "sed",
    "perl",
}
_COPY_COMMANDS = {"cp", "copy", "copy-item", "cpi", "xcopy"}


def _is_test_path(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    dirs = parts[:-1]
    if any(d in _TEST_DIRS or d.endswith((".tests", ".test")) or d.endswith("tests") for d in dirs):
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in _TEST_NAME_PATTERNS)


def _tester_may_write(rel: str) -> bool:
    """Тесты и вопросы по описанию блока, по более строгому правилу, чем общее `_is_test_path`:
    тестовая папка только с именем ровно из TESTER_TEST_DIRS или проект тестов C# `Имя.Tests`
    (файлы C#; не specs и не «оканчивается на tests», как `contests`),
    файл вне такой папки только по строгим маскам; всё под docs/ и путь с docs/specs закрыты."""
    low = rel.lower()
    if low in TESTER_EXTRA_PATHS:
        return True
    parts = PurePosixPath(low).parts
    if not parts:
        return False  # корень проекта (`git checkout -- .`, `rm -rf .`)
    pairs = list(zip(parts, parts[1:], strict=False))
    if parts[0] == "docs" or ("docs", "specs") in pairs:
        return False
    if parts[-1] in TESTER_CACHE_DIRS:
        return True  # сама папка кэша запуска тестов (`rm -rf .pytest_cache`); файлы в ней не нужны
    if any(d in TESTER_TEST_DIRS for d in parts):
        return True  # сама папка тестов (`cp x tests/`, `git checkout -- tests/`) и всё внутри
    if any(d.endswith(".tests") for d in parts[:-1]) and (
        parts[-1].endswith(TESTER_CS_SUFFIXES) or parts[-1] in {"bin", "obj"}
    ):
        return True  # проект тестов C# `Имя.Tests`: файлы C# и его сборка, не любой код в папке
    return any(fnmatch.fnmatch(parts[-1], pattern) for pattern in TESTER_NAME_PATTERNS)


def _is_accepted_adr(rel: str, project: Path) -> bool:
    # Имя в нижнем регистре могло не совпасть с реальным; ищем файл без учёта регистра.
    folder = project / PurePosixPath(rel).parent
    if not folder.is_dir():
        return False
    for candidate in folder.iterdir():
        if candidate.name.lower() == PurePosixPath(rel).name and candidate.is_file():
            head = candidate.read_text(encoding="utf-8", errors="replace")[:2000]
            return bool(_ADR_STATUS.search(head))
    return False


def _gated_reason(rel: str, project: Path) -> str | None:
    """Почему путь закрыт для всех, пока владелец не подтвердит; None, если не закрыт."""
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if parts[0] in (".claude", ".claude-plugin") or name == ".mcp.json":
        return "это настройки Claude и hooks: ими держатся все остальные проверки"
    if parts[0] in _CI_DIRS or (len(parts) == 1 and name in _CI_ROOT_FILES):
        return "это CI-файл: он определяет, что считается «готово»"
    if len(parts) >= 2 and parts[-2] == "state" and name in _STATE_FILES:
        return (
            f"state/{name} относится к проверкам CI («храповик»): его нельзя менять, "
            "чтобы пропустить нарушение"
        )
    if name == "constitution.md":
        return "CONSTITUTION.md (стек, список пакетов) утверждает только владелец"
    if name == "goal.md" and len(parts) >= 2 and parts[-2] == "docs":
        return "GOAL.md (цель продукта) меняет и утверждает только владелец"
    if len(parts) >= 3 and parts[-3:-1] == ("docs", "adr") and name.endswith(".md"):
        if _is_accepted_adr(rel, project):
            return "принятый ADR неизменяем: решение пересматривается новым ADR"
    return None


_ACCEPTED_WORD = re.compile(r"(?i)\b(accepted|принят\w*|утвержд\w*)\b")


def _strings(value: object) -> list[str]:
    """Все строки внутри значения (для поиска статуса в содержимом правки)."""
    if isinstance(value, str):
        return [value]
    items: list[object] = []
    if isinstance(value, dict):
        items = list(value.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    elif isinstance(value, list):
        items = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return [text for item in items for text in _strings(item)]


def _is_adr(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return len(parts) >= 3 and parts[-3:-1] == ("docs", "adr") and parts[-1].endswith(".md")


def _sets_accepted_status(data: JsonDict, command: str) -> bool:
    """Правка пытается перевести ADR в accepted или создать его уже принятым."""
    fields = tool_input(data)
    texts = [command] if command else _strings(fields)
    if any(_ADR_STATUS.search(text) for text in texts):
        return True
    old = " ".join(_strings(fields.get("old_string")))
    new = " ".join(_strings(fields.get("new_string")))
    return "proposed" in (old or command).lower() and bool(_ACCEPTED_WORD.search(new or command))


def _ci_module() -> ModuleType:
    """Единый источник правил про файлы настроек проверок: скрипт CI из шаблонов плагина."""
    folder = str(Path(__file__).resolve().parents[1] / "templates" / "ci" / "parch")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module("parch_ci")


def _simulated_text(data: JsonDict, current: str) -> str | None:
    """Текст файла после правки инструментом Write, Edit или MultiEdit; None, если не понять."""
    fields = tool_input(data)
    tool = get_str(data, "tool_name")
    if tool == "Write":
        content = fields.get("content")
        return content if isinstance(content, str) else None
    edits: list[object] = [fields]
    if tool == "MultiEdit":
        listed = fields.get("edits")
        edits = list(listed) if isinstance(listed, list) else []  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    text = current
    for item in edits:
        step = cast("dict[str, object]", item) if isinstance(item, dict) else {}
        old, new = step.get("old_string"), step.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or old not in text:
            return None
        text = text.replace(old, new) if step.get("replace_all") else text.replace(old, new, 1)
    return text


def _settings_reason(rel: str, real: Path, data: JsonDict, command: str) -> str | None:
    """Правка касается настроек проверок (линтер, типы, тесты, покрытие, архитектура)."""
    ci = _ci_module()
    base = (
        "это настройки проверок: ослаблять линтер, типы, тесты, покрытие или правила архитектуры "
        "можно только по решению владельца"
    )
    if ci.is_pure_config(rel):
        return base
    if not ci.is_container(rel):
        return None
    if command:
        return base  # правка через оболочку: какой раздел меняется, не понять
    current = real.read_text(encoding="utf-8", errors="replace") if real.is_file() else ""
    after = _simulated_text(data, current)
    if after is None:
        return None
    if ci.settings_of_text(rel, current) != ci.settings_of_text(rel, after):
        return f"в этом файле меняются настройки проверок ({base})"
    return None


def _decide(
    rel: str, real: Path, role: str, project: Path, data: JsonDict, command: str
) -> Block | Ask | None:
    if role == "tester" and not _tester_may_write(rel):
        return Block(
            HOOK,
            f"Роль tester правит только тесты и docs/QUESTIONS.md, а {rel} к ним не относится: "
            "код блока, CI, настройки проверок и baseline правят другие роли. Если для теста нужна "
            "правка кода, остановись и опиши её в ответе: её сделает исполнитель (implementer) "
            "или основная сессия.",
        )
    if _is_test_path(rel) and role not in TEST_ROLES:
        shown = f"«{role}»" if role else "основная сессия без роли"
        return Block(
            HOOK,
            f"Тесты ({rel}) правят только роли architect и tester, а у тебя {shown}. "
            "Тест — это требование, а не деталь реализации. Опиши нужную правку в ответе: "
            "её сделает architect или tester.",
        )
    gated = _gated_reason(rel, project)
    if gated is None and _is_adr(rel) and _sets_accepted_status(data, command):
        gated = "статус ADR accepted ставит только владелец: агент оставляет proposed"
    if gated is None:
        gated = _settings_reason(rel, real, data, command)
    if gated:
        return Ask(
            HOOK,
            f"Нужно подтверждение владельца: изменяется защищённый путь {rel}, {gated}. "
            "Подтверждай, только если сам просил именно эту правку.",
        )
    return None


def _is_product_repo(project: Path) -> bool:
    """Репозиторий самого продукта: в корне есть плагин (`plugin/.claude-plugin/plugin.json`)."""
    return (project / "plugin" / ".claude-plugin" / "plugin.json").is_file()


def _is_templates_root(project: Path) -> bool:
    """Корень проекта это сама `plugin/templates/` репозитория продукта.

    В шаблонах лежит `docs/CONSTITUTION.md`, поэтому для файла внутри `plugin/templates/` охрана
    путей считает корнем проекта именно эту папку, а не корень репозитория.
    """
    return (
        project.name.lower() == "templates"
        and project.parent.name.lower() == "plugin"
        and _is_product_repo(project.parent.parent)
    )


def _md_creation_allowed(rel: str, project: Path) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1]
    if "docs" in parts[:-1]:
        return True
    if _is_templates_root(project):
        return True  # шаблоны для целевых проектов (ADR-0016); путь уже приведён без `..`
    if parts[:2] == ("plugin", "templates") and _is_product_repo(project):
        return True  # то же, если корнем проекта считается корень репозитория
    if len(parts) == 1 and name in _ALLOWED_ROOT_MD:
        return True
    if name == "skill.md" and "skills" in parts[:-1]:
        return True
    if "agents" in parts[:-1] and name.endswith(".md"):
        return True
    return len(parts) >= 2 and parts[0] in {"analysis", "state"}


def _exists_ignoring_case(project: Path, rel: str) -> bool:
    folder = project / PurePosixPath(rel).parent
    if not folder.is_dir():
        return False
    name = PurePosixPath(rel).name
    return any(child.name.lower() == name for child in folder.iterdir())


def _md_block(rel: str, project: Path) -> Block | None:
    if not rel.endswith(".md") or _md_creation_allowed(rel, project):
        return None
    if _exists_ignoring_case(project, rel):
        return None
    return Block(
        HOOK,
        f"Нельзя создавать новые .md-файлы вне docs/: {rel}. Рабочие заметки и отчёты в "
        "репозиторий не кладём. Решения оформляй как ADR в docs/adr/, остальное напиши "
        "в ответе, а не в файле.",
    )


def _git_paths(tokens: list[str], strict: bool) -> list[str]:
    """Пути git-команды, которая меняет файлы. Грубо: всё без дефиса (для защищённых путей лишнее
    слово безвредно). strict: для правила «только своё» (tester) лишнее слово это ложный блок,
    поэтому берутся только надёжные пути: после rm, mv, restore и после `--` у checkout."""
    if not strict:
        return [t for t in tokens[1:] if not t.startswith(("-", ">"))]
    args = [t for t in tokens[1:] if not t.startswith(">")]
    subs = [i for i, t in enumerate(args) if t in {"checkout", "restore", "rm", "mv"}]
    if not subs:
        return []
    start = subs[0]
    if args[start] == "checkout":
        return args[args.index("--") + 1 :] if "--" in args[start:] else []
    rest = args[start + 1 :]
    paths: list[str] = []
    for index, token in enumerate(rest):
        after_source = index > 0 and rest[index - 1] in {"-s", "--source"}  # `--source main`
        if not token.startswith("-") and not after_source:
            paths.append(token)
    return paths


def _target_directory(tokens: list[str]) -> list[str]:
    """Цель `cp -t КУДА`: отдельным словом, слитно (`-tsrc`), в связке (`-vt src`) или через `=`."""
    found: list[str] = []
    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("--target-directory="):
            found.append(token.split("=", 1)[1])
        elif token == "--target-directory" and index + 1 < len(tokens):
            found.append(tokens[index + 1])
        elif re.fullmatch(r"-[a-zA-Z]*t.*", token) and not token.startswith("--"):
            rest = token[token.index("t") + 1 :]
            if rest:
                found.append(rest)
            elif index + 1 < len(tokens):
                found.append(tokens[index + 1])
    return found


def _files_after_scripts(tokens: list[str]) -> list[str]:
    """Файлы sed/perl без выражений: значения `-e`, `-pe`, `-f`, `--expression` не пути, а если
    выражений через флаги нет, выражением считается первое слово без дефиса."""
    files: list[str] = []
    skip_next = False
    had_script = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
        elif token.startswith(">"):
            continue
        elif token.startswith("-"):
            if token in {"--expression", "--file"} or re.fullmatch(r"-[a-zA-Z]*[ef]", token):
                skip_next = had_script = True
            elif token.startswith(("--expression=", "--file=")):
                had_script = True
        else:
            files.append(token)
    return files if had_script else files[1:]


def _in_place_flag(tokens: list[str]) -> bool:
    """sed/perl правят файл на месте: `-i[.bak]`, `-Ei`, `-pi.bak`, `--in-place[=.bak]`."""
    return any(
        t.startswith("--in-place") or (re.match(r"-[a-hj-zA-LN-Z]*i", t) is not None)
        for t in tokens[1:]
        if t.startswith("-")
    )


def _tester_shell_exempt(raw: str, rel: str, root: Path, command: str) -> bool:
    """Оболочка, tester: отчёты тестов, покрытие, временные файлы (как в guard_shell_writes).
    Имена отчётов принимаются только в корне проекта и в тестовых папках, `htmlcov` только верхней
    папкой: иначе `touch src/junit.xml` или `cp x src/htmlcov/y.py` обходили бы строгое правило."""
    cleaned = raw.strip("\"'")
    if not target_allowed(cleaned, root, command):
        return False
    if cleaned.startswith(TEMP_PREFIXES):
        # `$TEMP/x`: после склейки с cwd путь попал бы внутрь проекта; `$TEMP/../src/a.py` нет
        return ".." not in cleaned.replace("\\", "/").split("/")
    parts = PurePosixPath(rel).parts
    return len(parts) <= 1 or parts[0] in TESTER_TEST_DIRS | {"htmlcov"}


def _written_paths(tokens: list[str], strict: bool = False) -> list[str]:
    """Пути, которые команда (по грубой оценке) меняет, создаёт или удаляет."""
    name = command_name(tokens)
    paths: list[str] = []
    skip_next = False
    for index, token in enumerate(tokens[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if token in {">", ">>", "1>", "2>", "&>"} and index + 1 < len(tokens):
            if not (strict and tokens[index + 1].startswith("&")):  # `> &2`: поток, не файл
                paths.append(tokens[index + 1])
            skip_next = True
        elif token.startswith(">") and len(token) > 1:
            target = token.lstrip(">")
            if not (strict and target.startswith("&")):  # `>&2`: поток, не файл
                paths.append(target)
    positional = [t for t in tokens[1:] if not t.startswith(("-", ">"))]
    if name in _MUTATING_COMMANDS:
        if strict and name in {"sed", "perl"}:
            if not _in_place_flag(tokens):
                return paths
            positional = _files_after_scripts(tokens)
        elif name == "sed" and not any(t.startswith("-i") or t == "--in-place" for t in tokens):
            return paths
        elif name == "perl" and not any(t.startswith("-i") for t in tokens):
            return paths
        paths.extend(positional)
    elif name in _COPY_COMMANDS and positional:
        targets = _target_directory(tokens) if strict else []
        if targets:  # `cp -t КУДА что`: пишется только в КУДА, источники не меняются
            paths.extend(targets)
        else:
            paths.append(positional[-1])
    elif name == "git" and any(t in tokens for t in ("checkout", "restore", "rm", "mv")):
        paths.extend(_git_paths(tokens, strict))
    return paths


def _check_paths(
    raws: list[str], role: str, session_project: Path, data: JsonDict, command: str
) -> Block | Ask | None:
    asked: Ask | None = None
    cwd = Path(get_str(data, "cwd") or session_project)
    for raw in raws:
        cleaned = raw.strip("\"'").replace("\\", "/")
        if not Path(cleaned).is_absolute():
            cleaned = str(cwd / cleaned)  # относительный путь считается от текущей папки вызова
        for root in roots_for_path(cleaned, session_project):
            if not is_managed(root):
                continue
            rel = rel_posix(cleaned, root)
            if rel is None:
                continue
            if role == "tester" and command and _tester_shell_exempt(raw, rel, root, command):
                continue
            decision = _decide(rel, Path(cleaned), role, root, data, command)
            if isinstance(decision, Block):
                return decision
            if decision is not None and asked is None:
                asked = decision
            block = _md_block(rel, root)
            if block:
                return block
    return asked


def _tester_raws(command: str, cwd: Path) -> list[str]:
    """Пути записи команды для tester с учётом `cd` внутри команды: после `cd tests` путь
    `../src/x` ведёт в src, а `a.py` лежит в tests. Без `cd` пути остаются как написаны."""
    base = cwd
    raws: list[str] = []
    for tokens in command_tokens(command):
        if command_name(tokens) in CD_COMMANDS:
            args = [t for t in tokens[1:] if not t.startswith("-")]
            if args and not args[0].startswith(("$", "%", "~")):
                base = (base / args[0].strip("\"'").replace("\\", "/")).resolve()
            continue
        for raw in _written_paths(tokens, strict=True):
            plain = raw.strip("\"'").replace("\\", "/")
            moved = base != cwd and not Path(plain).is_absolute() and not plain.startswith("$")
            raws.append(str(base / plain) if moved else raw)
    return raws


def check(data: JsonDict, project: Path) -> Block | Ask | None:
    role = agent_role(data)
    tool = get_str(data, "tool_name")
    command = ""
    if tool in SHELL_TOOLS:
        command = shell_command(data)
        if role == "tester":
            raws = _tester_raws(command, Path(get_str(data, "cwd") or project))
        else:
            raws = [raw for tokens in command_tokens(command) for raw in _written_paths(tokens)]
    elif tool in FILE_TOOLS:
        raws = target_paths(data)
    else:
        return None
    return _check_paths(raws, role, project, data, command)


if __name__ == "__main__":
    run_guard(HOOK, check)
