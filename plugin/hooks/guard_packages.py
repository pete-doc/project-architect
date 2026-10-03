"""PreToolUse: установка пакетов только из списка разрешённых в CONSTITUTION.md.

Формат списка в CONSTITUTION.md (раздел «Разрешённые пакеты», по строке на экосистему):

    ## Разрешённые пакеты
    - pip: requests, pydantic
    - npm: react, @types/node
    - nuget: Newtonsoft.Json
    - psgallery: Pester, PSScriptAnalyzer

Новый пакет требует ADR; после утверждения владелец добавляет его в список.
Активен только в проектах с CONSTITUTION.md. Покрыты pip/uv/poetry/pipenv/pipx, npm/yarn/pnpm/bun
(включая npx), dotnet/nuget и Install-Module/Install-Package. Установка «всего из манифеста»
(`npm install`, `pip install -r` без файла) проверяется только для requirements-файлов.
"""

from __future__ import annotations

import re
from pathlib import Path

from _common import (
    SHELL_TOOLS,
    Block,
    JsonDict,
    cd_roots,
    command_name,
    command_tokens,
    constitution_path,
    get_str,
    is_managed,
    run_guard,
    shell_command,
)

HOOK = "guard_packages"

Request = tuple[str, str]  # (экосистема, имя пакета)

_ECOSYSTEM_ALIASES = {
    "pip": "pip",
    "pypi": "pip",
    "python": "pip",
    "npm": "npm",
    "node": "npm",
    "ts": "npm",
    "typescript": "npm",
    "nuget": "nuget",
    "dotnet": "nuget",
    "csharp": "nuget",
    "c#": "nuget",
    "psgallery": "psgallery",
    "powershell": "psgallery",
}
_HEADING = re.compile(
    r"^#{1,6}\s*(разрешённые пакеты|разрешенные пакеты|allowed packages)\s*$", re.I
)
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_PIP_VALUE_FLAGS = {
    "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f", "--find-links",
    "-t", "--target", "--prefix", "--root", "--python", "--platform", "--python-version",
    "--implementation", "--abi", "--cache-dir", "--src", "--upgrade-strategy", "--group",
    "--index", "--default-index", "--with", "-p",
}  # fmt: skip
_NPM_VALUE_FLAGS = {"--prefix", "-C", "--registry", "--workspace", "-w", "--cwd", "--filter"}
_NUGET_VALUE_FLAGS = {
    "-v", "--version", "-s", "--source", "-f", "--framework", "--package-directory",
    "--interactive", "-n", "--prerelease",
}  # fmt: skip


def normalize(ecosystem: str, name: str) -> str:
    name = name.strip().strip("`\"'")
    if ecosystem == "pip":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def load_allowed(constitution: Path) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    inside = False
    for line in constitution.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if _HEADING.match(stripped):
            inside = True
            continue
        if inside and _ANY_HEADING.match(stripped):
            break
        if not inside or not stripped.startswith(("-", "*")):
            continue
        label, _, names = stripped.lstrip("-* ").partition(":")
        ecosystem = _ECOSYSTEM_ALIASES.get(label.strip().strip("`").lower())
        if ecosystem is None:
            continue
        bucket = allowed.setdefault(ecosystem, set())
        bucket.update(normalize(ecosystem, n) for n in names.split(",") if n.strip())
    return allowed


# ---------- разбор команд установки ----------


def _positionals(args: list[str], value_flags: set[str]) -> list[str]:
    result: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in value_flags
        else:
            result.append(arg)
    return result


def _pip_name(spec: str) -> str | None:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return match.group(1) if match else None


def _pip_requirements(path: Path) -> list[Request]:
    requests: list[Request] = []
    if not path.is_file():
        return requests
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry or entry.startswith("-"):
            continue
        requests.append(("pip", _pip_name(entry) or entry))
    return requests


def _is_local(spec: str, project: Path) -> bool:
    return spec in {".", ".."} or (spec.startswith(("./", ".\\")) and (project / spec).is_dir())


def _pip_requests(args: list[str], project: Path) -> list[Request]:
    requests: list[Request] = []
    skip = False
    for index, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg in {"-r", "--requirement"} and index + 1 < len(args):
            requests.extend(_pip_requirements(project / args[index + 1]))
            skip = True
        elif arg.startswith("--requirement="):
            requests.extend(_pip_requirements(project / arg.split("=", 1)[1]))
        elif arg in {"-e", "--editable"}:
            skip = True  # локальный редактируемый путь; URL проверяется ниже
            if index + 1 < len(args) and "://" in args[index + 1]:
                requests.append(("pip", args[index + 1]))
        elif arg.startswith("-"):
            skip = arg in _PIP_VALUE_FLAGS
        elif _is_local(arg, project):
            continue
        elif "://" in arg or arg.startswith("git+") or arg.endswith((".whl", ".zip", ".gz")):
            requests.append(("pip", arg))
        else:
            requests.append(("pip", _pip_name(arg) or arg))
    return requests


def _after(tokens: list[str], *words: str) -> list[str] | None:
    """Аргументы после последовательности слов (например, `pip install`)."""
    lowered = [t.lower() for t in tokens]
    for start in range(len(tokens) - len(words) + 1):
        if lowered[start : start + len(words)] == list(words):
            return tokens[start + len(words) :]
    return None


def _npm_name(spec: str) -> str:
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        return f"@{scope}/{rest.split('@', 1)[0]}"
    return spec.split("@", 1)[0]


def _npm_requests(args: list[str], project: Path) -> list[Request]:
    requests: list[Request] = []
    for spec in _positionals(args, _NPM_VALUE_FLAGS):
        if spec in {".", ".."} or _is_local(spec, project):
            continue
        looks_foreign = re.match(r"^(git\+|https?:|file:|github:|[\w.-]+/[\w.-]+$)", spec)
        requests.append(("npm", spec if looks_foreign else _npm_name(spec)))
    return requests


def _nuget_requests(args: list[str]) -> list[Request]:
    return [("nuget", spec) for spec in _positionals(args, _NUGET_VALUE_FLAGS)[:1]]


def _powershell_requests(tokens: list[str], ecosystem: str) -> list[Request]:
    lowered = [t.lower() for t in tokens]
    for flag in ("-name", "-id"):
        if flag in lowered and lowered.index(flag) + 1 < len(tokens):
            names = tokens[lowered.index(flag) + 1]
            return [(ecosystem, n) for n in names.split(",") if n]
    positional = [t for t in tokens[1:] if not t.startswith("-")]
    return [(ecosystem, positional[0])] if positional else []


_REDIRECT_ALONE = re.compile(r"^(\d*|&)(>>?|<)$")
_REDIRECT_ATTACHED = re.compile(r"^(\d*|&)(>>?|<)\S")


def drop_redirections(tokens: list[str]) -> list[str]:
    """Убирает перенаправления вывода (`2>&1`, `> файл`, `2>/dev/null`): это не имена пакетов."""
    kept: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
        elif _REDIRECT_ALONE.match(token):
            skip_next = True
        elif _REDIRECT_ATTACHED.match(token):
            continue
        else:
            kept.append(token)
    return kept


def install_requests(tokens: list[str], project: Path) -> list[Request]:
    tokens = drop_redirections(tokens)
    name = command_name(tokens)
    if re.fullmatch(r"pip[\d.]*", name):
        args = _after(tokens, "install")
        return _pip_requests(args, project) if args is not None else []
    if name in {"python", "python3", "py"} or re.fullmatch(r"python[\d.]+", name):
        args = _after(tokens, "-m", "pip", "install")
        return _pip_requests(args, project) if args is not None else []
    if name == "uv":
        for words in (("pip", "install"), ("add",), ("tool", "install")):
            args = _after(tokens, *words)
            if args is not None:
                return _pip_requests(args, project)
    if name in {"poetry", "pipenv", "pipx"}:
        for word in ("add", "install"):
            args = _after(tokens, word)
            if args is not None:  # `poetry install` без аргументов ставит из lock-файла
                return _pip_requests(args, project)
        return []
    if name in {"npm", "yarn", "pnpm", "bun"}:
        for word in ("install", "i", "add", "dlx"):
            args = _after(tokens, word)
            if args is not None:
                return _npm_requests(args, project)
    if name in {"npx", "bunx"} and not any(t in {"--no-install", "--no"} for t in tokens):
        return _npm_requests(tokens[1:2] if len(tokens) > 1 else [], project)
    if name == "dotnet":
        args = _after(tokens, "package") if "add" in tokens else None
        if args is None and _after(tokens, "tool", "install") is not None:
            args = _after(tokens, "tool", "install")
        return _nuget_requests(args) if args is not None else []
    if name == "nuget":
        args = _after(tokens, "install")
        return _nuget_requests(args) if args is not None else []
    if name in {"install-package", "install-module", "install-psresource", "install-script"}:
        ecosystem = "nuget" if name == "install-package" else "psgallery"
        return _powershell_requests(tokens, ecosystem)
    return []


def check(data: JsonDict, project: Path) -> Block | None:
    if get_str(data, "tool_name") not in SHELL_TOOLS:
        return None
    # проект сессии и проекты, в папки которых команда переходит через cd: нельзя уйти из-под
    # защиты, перейдя в чужую папку, и нельзя промахнуться, если сессия в папке без CONSTITUTION.md
    for root in dict.fromkeys([project, *cd_roots(data)]):
        block = check_project(data, root)
        if block is not None:
            return block
    return None


def check_project(data: JsonDict, project: Path) -> Block | None:
    if not is_managed(project):
        return None
    constitution = constitution_path(project)
    if constitution is None:
        return None
    allowed = load_allowed(constitution)
    for tokens in command_tokens(shell_command(data)):
        for ecosystem, package in install_requests(tokens, project):
            if normalize(ecosystem, package) in allowed.get(ecosystem, set()):
                continue
            return Block(
                HOOK,
                f"Пакет «{package}» ({ecosystem}) не входит в список разрешённых в "
                f"{constitution.name}. Ставить новые пакеты самому нельзя: оформи ADR "
                "(docs/adr/, команда /parch:adr) с обоснованием, зачем он нужен и чем нельзя "
                "обойтись. После утверждения владелец добавит пакет в раздел «Разрешённые "
                "пакеты». Пока можно обойтись стандартной библиотекой.",
            )
    return None


if __name__ == "__main__":
    run_guard(HOOK, check)
