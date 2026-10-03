"""PreToolUse: блокирует необратимые команды и обращения к секретам.

Работает во всех проектах, подключён ли ProjectArchitect или нет. Для всех ролей.
Разбор команд приблизительный: надёжная защита от обхода — правила permissions
(см. plugin/templates/claude/settings.json) и песочница.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from _common import (
    SHELL_TOOLS,
    Block,
    JsonDict,
    command_name,
    command_tokens,
    get_str,
    run_guard,
    shell_command,
    target_paths,
)

HOOK = "guard_destructive"

_SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
_SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore", ".jks")
_SECRET_NAMES = {
    ".netrc",
    "_netrc",
    ".pypirc",
    ".npmrc",
    ".git-credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "secrets.json",
    "secrets.yml",
    "secrets.yaml",
    "secrets.toml",
}
_SECRET_DIRS = {".ssh", ".gnupg"}
_PS_RECURSE = re.compile(r"^-r(e(c(u(r(se?)?)?)?)?)?$", re.IGNORECASE)
_PS_FORCE = re.compile(r"^-fo(r(ce?)?)?$", re.IGNORECASE)
_PS_REMOVE = {"remove-item", "ri", "del", "erase", "rd", "rmdir", "rm"}
_UNRESOLVED = set("$%*?[]~`{}")


def is_secret_path(path: str) -> bool:
    """True, если путь похож на файл с секретами (ключи, .env, учётные данные)."""
    normalized = path.replace("\\", "/").strip("\"'")
    parts = [p.lower() for p in PurePosixPath(normalized).parts]
    if not parts:
        return False
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and not name.endswith(_SAFE_ENV_SUFFIXES)):
        return True
    if name in _SECRET_NAMES or name.endswith(_SECRET_SUFFIXES):
        return True
    if any(part in _SECRET_DIRS for part in parts[:-1]) or name in _SECRET_DIRS:
        return True
    return len(parts) >= 2 and parts[-2] == ".aws" and name in {"credentials", "config"}


def _short_flags(tokens: list[str]) -> str:
    return "".join(t[1:] for t in tokens[1:] if t.startswith("-") and not t.startswith("--"))


def _long_flags(tokens: list[str]) -> set[str]:
    return {t.lower() for t in tokens[1:] if t.startswith("--")}


def _deletes_recursively_and_forcibly(tokens: list[str]) -> bool:
    name = command_name(tokens)
    if name == "rm":
        shorts, longs = _short_flags(tokens), _long_flags(tokens)
        recursive = "r" in shorts.lower() or "--recursive" in longs
        forced = "f" in shorts or "--force" in longs
        if recursive and forced:
            return True
    if name in _PS_REMOVE:
        recursive = any(_PS_RECURSE.match(t) for t in tokens[1:])
        forced = any(_PS_FORCE.match(t) for t in tokens[1:])
        if recursive and forced:
            return True
    if name in {"rd", "rmdir", "del", "erase"}:
        lowered = [t.lower() for t in tokens[1:]]
        if "/s" in lowered:
            return True
    return False


def _inside_system_temp(target: str, project: Path) -> bool:
    """True, если путь заведомо лежит внутри системной временной папки и не задевает проект."""
    temp = Path(tempfile.gettempdir()).resolve()
    if _UNRESOLVED & set(target):
        return False  # только буквальные пути: переменную hook и оболочка раскрыли бы по-разному
    text = target
    if os.name == "nt" and re.match(r"^/[a-zA-Z]/", text):
        text = f"{text[1]}:{text[2:]}"  # путь Git Bash: /c/Users/... -> C:/Users/...
    if ".." in re.split(r"[\\/]", text) or not Path(text).is_absolute():
        return False
    path = Path(text).resolve()
    if path == temp or temp not in path.parents:
        return False
    root = project.resolve()
    return not (path == root or path in root.parents or root in path.parents)


def _removes_only_system_temp(tokens: list[str], project: Path) -> bool:
    """Удаление, у которого все цели лежат внутри системной временной папки вне проекта."""
    if any(t.lower() == "/s" for t in tokens[1:]):
        return False  # форма cmd (rd /s) целей не разбираем
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    return bool(targets) and all(_inside_system_temp(t, project) for t in targets)


def _force_pushes(tokens: list[str]) -> bool:
    if command_name(tokens) != "git" or "push" not in tokens[1:]:
        return False
    for token in tokens[tokens.index("push") + 1 :]:
        lowered = token.lower()
        if lowered in {"--force", "-f"} or lowered.startswith("--force-with-lease"):
            return True
        if re.fullmatch(r"-[a-z]*f[a-z]*", lowered) and not lowered.startswith("--"):
            return True
        if token.startswith("+") and len(token) > 1:
            return True
    return False


def _git_subcommand_args(tokens: list[str], subcommand: str) -> list[str] | None:
    if command_name(tokens) != "git" or subcommand not in tokens[1:]:
        return None
    return tokens[tokens.index(subcommand) + 1 :]


def _git_reset_hard(tokens: list[str]) -> bool:
    args = _git_subcommand_args(tokens, "reset")
    return args is not None and "--hard" in [a.lower() for a in args]


def _git_clean_destroys(tokens: list[str]) -> bool:
    """git clean с -f, -x, -X или -d (по отдельности или вместе, например -fdx) стирает файлы."""
    args = _git_subcommand_args(tokens, "clean")
    if args is None:
        return False
    lowered = [a.lower() for a in args]
    if "--dry-run" in lowered or any(re.fullmatch(r"-[a-z]*n[a-z]*", a) for a in args):
        return False  # пробный запуск ничего не удаляет
    if "--force" in lowered:
        return True
    return any(
        re.fullmatch(r"-[fxXd]+[a-zA-Z]*", a) or re.fullmatch(r"-[a-zA-Z]*[fxXd]", a) for a in args
    )


def _dumps_environment(tokens: list[str]) -> bool:
    name = command_name(tokens)
    if name == "printenv":
        return True
    if name == "env" and len(tokens) == 1:
        return True
    lowered = [t.lower() for t in tokens]
    return name in {"get-childitem", "gci", "dir", "ls"} and any(
        t.startswith("env:") for t in lowered[1:]
    )


def _command_block(command: str, project: Path) -> Block | None:
    for tokens in command_tokens(command):
        if _deletes_recursively_and_forcibly(tokens) and not _removes_only_system_temp(
            tokens, project
        ):
            return Block(
                HOOK,
                "Рекурсивное принудительное удаление (rm -rf, Remove-Item -Recurse -Force, "
                "rd /s) запрещено: его нельзя отменить. Удаляй конкретные файлы по одному "
                "или через git rm, чтобы удаление осталось в истории. Внутри системной временной "
                "папки вне проекта удалять можно.",
            )
        if _force_pushes(tokens):
            return Block(
                HOOK,
                "git push --force запрещён: он переписывает общую историю и может стереть "
                "чужую работу. Сделай обычный push; если ветка разошлась, смёржи или "
                "перебазируй локально и спроси владельца.",
            )
        if _git_reset_hard(tokens):
            return Block(
                HOOK,
                "git reset --hard запрещён: он стирает несохранённые изменения без возможности "
                "вернуть. Сохрани работу коммитом или git stash; чтобы отменить один файл, "
                "спроси владельца.",
            )
        if _git_clean_destroys(tokens):
            return Block(
                HOOK,
                "git clean с флагами -f, -x или -d запрещён: он безвозвратно удаляет файлы, "
                "которых нет в git. Сначала посмотри, что будет удалено: git clean -n; "
                "удалять конкретные файлы нужно по одному.",
            )
        if _dumps_environment(tokens):
            return Block(
                HOOK,
                "Вывод всех переменных окружения запрещён: в них лежат секреты. "
                "Спроси конкретную переменную, которая нужна, у владельца.",
            )
        for token in tokens[1:]:
            cleaned = token.lstrip(">")
            if cleaned and is_secret_path(cleaned):
                return Block(
                    HOOK,
                    f"Обращение к файлу с секретами запрещено: {cleaned}. "
                    "Секреты агент не читает и не меняет. Если нужна настройка, "
                    "используй .env.example или спроси владельца.",
                )
    return None


def check(data: JsonDict, project: Path) -> Block | None:
    if get_str(data, "tool_name") in SHELL_TOOLS:
        return _command_block(shell_command(data), project)
    for path in target_paths(data):
        if is_secret_path(path):
            return Block(
                HOOK,
                f"Обращение к файлу с секретами запрещено: {path}. "
                "Секреты агент не читает и не меняет. Если нужна настройка, "
                "используй .env.example или спроси владельца.",
            )
    return None


if __name__ == "__main__":
    run_guard(HOOK, check)
