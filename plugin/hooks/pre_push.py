"""PreToolUse: перед `git push` запускается проверка `standard` (STANDARD.md, 7.2, правило 3).

Правило 3: всё, что ловит CI, сначала ловит hook на машине исполнителя до push. Hook работает
бесплатно, CI платный: нарушение таймаута, отмены прогонов, триггеров или раннеров в workflow
останавливается здесь, а не после запуска CI. Проверка та же, что в CI (`parch_ci.py standard`).

Активен в проектах с CONSTITUTION.md и в самом репозитории продукта (у него CONSTITUTION.md нет).
Если проверка сама сломалась, push тоже блокируется (общий принцип охранных hooks).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

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

HOOK = "pre_push"


def _ci_module() -> ModuleType:
    """Единый источник правил: скрипт CI из шаблонов плагина (так же, как в guard_paths)."""
    folder = str(Path(__file__).resolve().parents[1] / "templates" / "ci" / "parch")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module("parch_ci")


def is_product_repo(project: Path) -> bool:
    """Репозиторий самого продукта: в нём CONSTITUTION.md нет, но есть плагин и маркетплейс."""
    return (project / ".claude-plugin" / "marketplace.json").is_file() and (
        project / "plugin" / "hooks" / "hooks.json"
    ).is_file()


_GIT_OPTIONS_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def git_subcommand(tokens: list[str]) -> str:
    """Подкоманда git: первый токен без `-`, кроме значений общих опций (`-C папка`)."""
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
        elif token in _GIT_OPTIONS_WITH_VALUE:
            skip = True
        elif not token.startswith("-"):
            return token
    return ""


def is_push(tokens: list[str]) -> bool:
    """`git push`, а не `git commit -m push` и не слово push в тексте команды."""
    return command_name(tokens) == "git" and git_subcommand(tokens) == "push"


def check(data: JsonDict, project: Path) -> Block | None:
    if get_str(data, "tool_name") not in SHELL_TOOLS:
        return None
    if not any(is_push(tokens) for tokens in command_tokens(shell_command(data))):
        return None
    if not (is_managed(project) or is_product_repo(project)):
        return None
    result = _ci_module().check_standard(project, "python")
    if result.ok:
        return None
    details = "\n".join(result.lines)
    return Block(
        HOOK,
        "Push остановлен: проверка `standard` нашла нарушения. CI нашёл бы их позже и потратил "
        "бы квоту минут (каждый платный прогон лишний), поэтому исправь их до push.\n" + details,
    )


if __name__ == "__main__":
    run_guard(HOOK, check)
