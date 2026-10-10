# ruff: noqa: E501
"""Правила состава проекта для проверки `standard` (F14, PR 1; STANDARD.md, раздел 11).

Три правила: обязательные файлы на месте; нет конкурирующих файлов инструкций для ИИ; нет `.md` вне разрешённых мест.
Применяются только к подключённому проекту: у него есть CONSTITUTION.md (docs/ или корень, как в hooks) или установлен
`.github/parch/parch_ci.py` (в репозитории самого продукта скрипт лежит в `plugin/templates/`, а CONSTITUTION подключает его
к собственным правилам, F25).

Строгость (решение владельца, 2026-10-05): на новом проекте нарушение это провал. Проект, записанный как существующий
(`existing_project: true` в `state/baseline.json`, его ставит init-project, если при подключении уже был код), сначала только
получает предупреждение. Когда владелец запишет долг (`standard --update --accept-new`, файл `state/standard-baseline.json`),
включается храповик: записанные нарушения допускаются, число может только снижаться, новое нарушение падает.

Здесь же (F14, PR 3) пункты карточки соответствия P1–P13 (раздел 9 стандарта; оценивает их `compliance_card` в
parch_ci.py) и версия стандарта проекта: что изменилось с версии, записанной в CONSTITUTION.md.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import cast

DEBT_FILE = "state/standard-baseline.json"
REQUIRED = (
    ("цель продукта", ("docs/GOAL.md", "GOAL.md")),
    ("конституция проекта", ("docs/CONSTITUTION.md", "CONSTITUTION.md")),
    ("карта модулей", ("docs/MODULES.md",)),
    ("реестр блоков", ("state/features.json",)),
    ("шаблон отчёта об инциденте", ("docs/INCIDENT_TEMPLATE.md",)),
)
COMPETING = (
    ".cursorrules", ".windsurfrules", ".clinerules", "GEMINI.md", ".github/copilot-instructions.md",
)  # fmt: skip
COMPETING_DIRS = (".cursor/rules",)
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", ".tox",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
}  # fmt: skip  # только зависимости и кэши; сборочные каталоги в чистой выгрузке CI не лежат
ROOT_MD = {
    "agents.md",
    "claude.md",
    "readme.md",
    "goal.md",
    "constitution.md",
}  # две последних допустимы вместо docs/
ALLOWED_TOP = {"docs", "state", "analysis", ".github", ".claude"}


CONSTITUTION_CANDIDATES = (
    "docs/CONSTITUTION.md",
    "CONSTITUTION.md",
)  # те же, что в plugin/hooks/_common.py


def is_managed(project: Path) -> bool:
    """Подключённый проект: есть CONSTITUTION.md (как в hooks) или установлен `.github/parch/parch_ci.py`."""
    return (
        exists_any(project, CONSTITUTION_CANDIDATES)
        or (project / ".github" / "parch" / "parch_ci.py").is_file()
    )


def exists_any(project: Path, options: tuple[str, ...]) -> bool:
    return any((project / o).is_file() for o in options)


def file_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for title, options in REQUIRED:
        if not exists_any(project, options):
            found[f"file:{options[0]}"] = (
                f"нет файла «{title}» ({' или '.join(options)}): создайте его по шаблону продукта "
                "(init-project кладёт шаблоны)"
            )
    return found


def instruction_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in COMPETING:
        if (project / name).is_file():
            found[f"instructions:{name}"] = (
                f"{name}: второй файл инструкций для ИИ рядом с AGENTS.md; оставьте одно место (AGENTS.md)"
            )
    for folder in COMPETING_DIRS:
        base = project / folder
        if base.is_dir():
            for path in sorted(p for p in base.rglob("*") if p.is_file()):
                rel = path.relative_to(project).as_posix()
                found[f"instructions:{rel}"] = (
                    f"{rel}: правила для ИИ вне AGENTS.md; перенесите их в AGENTS.md"
                )
    claude, agents = project / "CLAUDE.md", project / "AGENTS.md"
    if claude.is_file() and agents.is_file():
        text = claude.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"^\s*@AGENTS\.md\s*$", text, re.MULTILINE):
            found["instructions:CLAUDE.md"] = (
                "CLAUDE.md не ссылается на AGENTS.md: две разные инструкции расходятся; "
                "оставьте в CLAUDE.md строку `@AGENTS.md` и правила Claude Code"
            )
    return found


def markdown_allowed(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1].lower()
    if len(parts) == 1:
        return name in ROOT_MD
    if parts[0].lower().startswith("parch-analysis") or parts[0] in ALLOWED_TOP:
        return True
    if parts[:-1] in (("agents",), ("plugin", "agents")):
        return True
    return name == "skill.md" and len(parts) >= 3 and parts[-3] == "skills"


def markdown_violations(project: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(project.rglob("*.md")):
        parts = path.relative_to(project).parts
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        rel = path.relative_to(project).as_posix()
        if not markdown_allowed(rel):
            found[f"md:{rel}"] = (
                f"{rel}: .md вне разрешённых мест (docs/, state/, корневые README, AGENTS, CLAUDE)"
            )
    return found


GOAL_ID = re.compile(r"\bG\d+\b")
ACCEPTED_VERDICT = re.compile(r"Итог:\s*принято владельцем,\s*\d{4}-\d{2}-\d{2}")
BASIS_HEADING = "Основания"
BASIS_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
BASIS_SOURCE = re.compile(
    r"каталог|capabilities|реестр модулей|modules|\badr\b|adr/|git log|поиск|search|lessons|урок|incidents|"
    r"истори|ссылк|https?://",
    re.IGNORECASE,
)  # fmt: skip


def strings(value: object) -> list[str]:
    """Непустые элементы списка как строки (не список: пусто)."""
    if not isinstance(value, list):
        return []
    return [str(v) for v in cast("list[object]", value) if v]


def accepted_by_owner(project: Path, block_id: str) -> bool:
    """Блок без тестов принят владельцем: все критерии отмечены и стоит строка итога."""
    path = project / "state" / "acceptance" / f"{block_id}.md"
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return bool(ACCEPTED_VERDICT.search(text)) and "- [ ]" not in text


def features_violations(project: Path) -> dict[str, str]:
    """Связность реестра блоков (раздел 3): цель, зависимости, цикл, «готово» без тестов приёмки."""
    path = project / "state" / "features.json"
    if not path.is_file():
        return {}  # отсутствие файла ловит правило обязательных файлов
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return {"features:unreadable": "state/features.json не читается как JSON"}
    items = cast("dict[str, object]", data).get("features") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"features:unreadable": "в state/features.json нет списка features"}
    blocks = [
        cast("dict[str, object]", i) for i in cast("list[object]", items) if isinstance(i, dict)
    ]
    ids = [str(b.get("id", "")) for b in blocks]
    found: dict[str, str] = {}
    goal_ids: set[str] = set()
    for name in ("docs/GOAL.md", "GOAL.md"):
        goal_file = project / name
        if goal_file.is_file():
            goal_ids |= set(
                GOAL_ID.findall(goal_file.read_text(encoding="utf-8", errors="replace"))
            )
    graph: dict[str, list[str]] = {}
    for number, block in enumerate(blocks, start=1):
        bid = str(block.get("id", ""))
        if not bid:
            found[f"features:no-id:{number}"] = f"в state/features.json блок №{number} без id"
            continue
        if ids.count(bid) > 1:
            found[f"features:{bid}:duplicate"] = f"блок {bid}: id повторяется в state/features.json"
        goals = strings(block.get("goal"))
        if not goals:
            found[f"features:{bid}:no-goal"] = (
                f"блок {bid}: нет цели (поле goal пусто): код без цели"
            )
        elif goal_ids:
            for goal in goals:
                if goal not in goal_ids:
                    found[f"features:{bid}:goal:{goal}"] = (
                        f"блок {bid}: цели {goal} нет в документе цели"
                    )
        deps = strings(block.get("depends_on"))
        graph[bid] = deps
        for dep in deps:
            if dep not in ids:
                found[f"features:{bid}:dep:{dep}"] = (
                    f"блок {bid}: зависит от несуществующего блока {dep}"
                )
        tests = block.get("acceptance_tests")
        if block.get("status") == "done" and not (isinstance(tests, list) and tests):
            if not accepted_by_owner(project, bid):
                found[f"features:{bid}:done-no-tests"] = (
                    f"блок {bid}: статус «готово» без тестов приёмки и без принятия владельцем "
                    "(state/acceptance/)"
                )
    for group in cyclic_groups(graph):
        found["features:cycle:" + "+".join(group)] = (
            "цикл зависимостей между блоками: " + ", ".join(group)
        )
    return found


def cyclic_groups(graph: dict[str, list[str]]) -> list[list[str]]:
    """Все циклы: группы блоков, зависящих друг от друга по кругу (компоненты связности Тарьяна)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    groups: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if dep not in index:
                visit(dep)
                low[node] = min(low[node], low[dep])
            elif dep in on_stack:
                low[node] = min(low[node], index[dep])
        if low[node] == index[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                members.append(member)
                if member == node:
                    break
            if len(members) > 1 or node in graph.get(node, []):
                groups.append(sorted(members))

    for node in graph:
        if node not in index:
            visit(node)
    return sorted(groups)


BASIS_LABEL = re.compile(rf"^{BASIS_HEADING}:[ \t]*(.*)$", re.IGNORECASE)
BASIS_FILLER = re.compile(
    r"^[\s\-–—_*•.]*$"
)  # строки из одних прочерков и пунктуации текстом не считаются


def basis_section_lines(body: str) -> list[str] | None:
    """Строки раздела «Основания» в тексте (None: раздела нет)."""
    lines = BASIS_COMMENT.sub("", body).splitlines()
    section: list[str] | None = None
    level = 0
    plain = False  # раздел начат строкой «Основания:» без решётки (git вырезает строки с # в сообщении из редактора)
    fenced = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced  # `#` внутри блока кода не заголовок
        heading = None if fenced else re.match(r"^(#{1,6})\s+(.*)$", line)
        if section is None and not fenced and (label := BASIS_LABEL.match(line)):
            section, plain = [label[1]] if label[1].strip() else [], True
            continue
        if plain and section is not None:
            blank = not line.strip()
            if heading or (blank and section and not section[-1].strip()):
                break  # без решётки раздел кончается заголовком с # или двумя пустыми строками подряд
            if blank and not section:
                section.append(
                    line
                )  # пустая строка сразу после «Основания:» (первая из возможных двух)
                continue
        if heading:
            if section is not None and len(heading[1]) <= level:
                break  # раздел кончается заголовком того же или более высокого уровня
            if section is None and heading[2].strip().lower() == BASIS_HEADING.lower():
                section, level = [], len(heading[1])
                continue
        if section is not None and not heading:  # сами подзаголовки текстом не считаются
            section.append(line)
    return section


def basis_problem(body: str, where: str = "в описании PR") -> str | None:
    """Раздел «Основания» в тексте (описание PR или сообщение коммита): есть, не пуст, назван источник."""
    section = basis_section_lines(body)
    if section is None:
        return f"{where} нет раздела «{BASIS_HEADING}» (образец: шаблон .github/pull_request_template.md)"
    text = "\n".join(line for line in section if not BASIS_FILLER.match(line)).strip()
    if not text:
        return f"раздел «{BASIS_HEADING}» {where} пуст или из одних прочерков: протокол исполнителя не пройден"
    if not BASIS_SOURCE.search(text):
        return (
            f"раздел «{BASIS_HEADING}» {where} не называет источник (каталог, реестр модулей, ADR, git log, "
            "поиск по коду): напишите, что искали и что нашли"
        )
    return None


COMMITS_WHERE = "в сообщениях коммитов ветки"


def commit_messages(project: Path, base_ref: str) -> tuple[list[str], str | None]:
    """Сообщения коммитов ветки относительно основной (`git log base..HEAD`) и пояснение, если история недоступна."""
    try:
        done = subprocess.run(
            ["git", "log", "--format=%B%x00", f"{base_ref}..HEAD"],
            cwd=project, capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as error:
        return [], f"не удалось запустить git для истории ветки: {error}"
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()[:1]
        return [], (
            f"история ветки от {base_ref} недоступна ({reason[0] if reason else 'git log завершился ошибкой'}): "
            f"шаг fetch-main должен подтянуть {base_ref} и всю историю ветки"
        )
    return [m.strip() for m in done.stdout.split("\x00") if m.strip()], None


def commits_basis_text(messages: list[str]) -> str | None:
    """Раздел «Основания» из последнего коммита ветки, где он корректен (для описания PR: пишется один раз, в коммите)."""
    for message in reversed(messages):
        if basis_problem(message, COMMITS_WHERE) is None:
            section = basis_section_lines(message) or []
            return f"## {BASIS_HEADING}\n\n" + "\n".join(section).strip() + "\n"
    return None


def commits_basis_problem(messages: list[str], error: str | None = None) -> str | None:
    """«Основания» хотя бы в одном коммите ветки (не в каждом); без новых коммитов или без истории проверка красная."""
    if error is not None:
        return error
    if not messages:
        return (
            "в ветке нет новых коммитов относительно основной: раздел «Основания» писать негде "
            "(он берётся из сообщений коммитов, `git log main..HEAD`)"
        )
    problems = [basis_problem(message, COMMITS_WHERE) for message in messages]
    if any(problem is None for problem in problems):
        return None
    last = next(p for p in reversed(problems) if p is not None)
    return f"ни в одном из {len(messages)} коммитов ветки нет корректного раздела «{BASIS_HEADING}»; последний коммит: {last}"


PROTECTED_HEADING = "Защищ[её]нные файлы"  # шаблон регулярного выражения: «ё» и «е»
PROTECTED_SECTION = "Защищённые файлы, изменённые в PR"
PROTECTED_PATHS = (
    ".github/", ".circleci/", ".claude/", "state/", "docs/CONSTITUTION.md", "CONSTITUTION.md",
    "docs/GOAL.md", "AGENTS.md", "CLAUDE.md", ".importlinter", "pyproject.toml",
)  # fmt: skip
PROTECTED_EXEMPT = (
    "state/incidents/",
)  # отчёты об инцидентах пишет исполнитель, это не файлы владельца
PROTECTED_LABEL = re.compile(rf"^\**{PROTECTED_HEADING}[^:\n]*:\**\s*(.*)$", re.IGNORECASE)
PROTECTED_MD_HEADING = re.compile(rf"^#{{1,6}}\s+\**{PROTECTED_HEADING}\b", re.IGNORECASE)


def is_protected(path: str) -> bool:
    """Файл, который в проекте утверждает владелец: CI, правила, настройки Claude, state/, цель и инструкции."""
    path = path.replace("\\", "/")
    if any(path.startswith(prefix) for prefix in PROTECTED_EXEMPT):
        return False
    return any(
        path == item or (item.endswith("/") and path.startswith(item)) for item in PROTECTED_PATHS
    )


def changed_files(project: Path, base_ref: str) -> tuple[list[str], str | None]:
    """Файлы, изменённые веткой относительно основной (`git diff base...HEAD`), и пояснение, если история недоступна."""
    try:
        done = subprocess.run(
            # --no-renames: при переименовании видны оба пути, `-z`: имена не в кавычках (не-ASCII)
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base_ref}...HEAD"],
            cwd=project, capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as error:
        return [], f"не удалось запустить git для списка изменённых файлов: {error}"
    if done.returncode != 0:
        reason = (done.stderr or done.stdout).strip().splitlines()[:1]
        return [], (
            f"список изменённых файлов от {base_ref} недоступен "
            f"({reason[0] if reason else 'git diff завершился ошибкой'})"
        )
    return [name for name in done.stdout.split("\0") if name.strip()], None


def protected_section_text(message: str) -> str:
    """Текст раздела «Защищённые файлы…» в сообщении коммита (`## Защищённые файлы…` или строка «Защищённые файлы…:»)."""
    section: list[str] = []
    inside = False
    plain = False
    blank = 0
    fenced = False
    for line in message.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        heading = not fenced and re.match(r"^#{1,6}\s", line) is not None
        if not inside:
            if not fenced and PROTECTED_MD_HEADING.match(line):
                inside, plain = True, False
            elif not fenced and (label := PROTECTED_LABEL.match(line)):
                inside, plain = True, True
                section.append(label[1])
            continue
        if heading or (plain and blank >= 2):
            break
        blank = blank + 1 if not line.strip() else 0
        section.append(line)
    return "\n".join(section).strip()


def mentioned(name: str, text: str) -> bool:
    """`name` стоит в тексте отдельным путём: `AGENTS.md` не засчитывается в `docs/AGENTS.md` и `NOT_AGENTS.md`."""
    # после имени папки не должно идти продолжение пути: `state/features.json` не называет папку `state/`
    pattern = rf"(?<![\w./-]){re.escape(name)}" + (
        r"(?!\w)" if name.endswith("/") else r"(?![\w/])"
    )
    return re.search(pattern, text) is not None


def named_in(path: str, text: str) -> bool:
    """Файл назван сам или его защищённой папкой (`.github/parch/`, `state/`); `docs/` и `src/` не защищены, не в счёт."""
    path = path.replace("\\", "/")
    if mentioned(path, text):
        return True
    return any(
        is_protected(f"{parent}/") and mentioned(f"{parent}/", text)
        for parent in PurePosixPath(path).parents
        if str(parent) != "."
    )


def commits_protected_problem(messages: list[str], changed: list[str]) -> str | None:
    """Если ветка меняет защищённые файлы, в коммитах есть раздел с их названием и причиной (владелец утверждает такие PR)."""
    touched = [path for path in changed if is_protected(path)]
    if not touched:
        return None
    text = "\n".join(filter(None, (protected_section_text(m) for m in messages)))
    names = ", ".join(touched[:5]) + (f" и ещё {len(touched) - 5}" if len(touched) > 5 else "")
    if not text.strip():
        return (
            f"ветка меняет защищённые файлы ({names}), но в сообщениях коммитов нет раздела «{PROTECTED_SECTION}»: "
            "назовите каждый файл (или папку) и зачем он изменён; такой PR утверждает владелец"
        )
    unnamed = [path for path in touched if not named_in(path, text)]
    if unnamed:
        shown = ", ".join(unnamed[:5]) + (f" и ещё {len(unnamed) - 5}" if len(unnamed) > 5 else "")
        return f"в разделе «{PROTECTED_SECTION}» не названы изменённые защищённые файлы: {shown}"
    return None


def read_debt(project: Path) -> set[str] | None:
    path = project / DEBT_FILE
    if not path.is_file():
        return None
    data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    raw = cast("dict[str, object]", data).get("violations") if isinstance(data, dict) else None
    return {str(x) for x in cast("list[object]", raw)} if isinstance(raw, list) else set()


def write_debt(project: Path, debt: set[str]) -> None:
    """Файл остаётся и при пустом долге: иначе существующий проект вернулся бы к предупреждению."""
    path = project / DEBT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"version": 1, "violations": sorted(debt)}, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def existing_project(project: Path) -> bool:
    path = project / "state" / "baseline.json"
    if not path.is_file():
        return False
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return False
    return (
        isinstance(data, dict) and cast("dict[str, object]", data).get("existing_project") is True
    )


def check(
    project: Path,
    update: bool = False,
    accept_new: bool = False,
    extra: dict[str, str] | None = None,
    pr_body: str | None = None,
    commits: tuple[list[str], str | None] | None = None,
    changed: tuple[list[str], str | None] | None = None,
) -> tuple[list[str], list[str]]:
    """(провалы, пометки): правила состава проекта в трёх режимах: новый проект, существующий без долга, с храповиком.

    `extra` — нарушения, которые находит вызывающий (отчёты об инцидентах, ключ `incident:<имя>`): они идут
    через тот же долг. `pr_body` — описание PR (старые проекты на Actions); `commits` — (сообщения коммитов ветки, пояснение
    об ошибке истории) для проектов на CircleCI (ADR-0022); `changed` — (файлы, изменённые веткой, пояснение об ошибке):
    если среди них защищённые, в коммитах нужен раздел «Защищённые файлы, изменённые в PR». None: проверка «Оснований»
    и защищённых файлов не идёт (локальный запуск).
    """
    if not is_managed(project):
        return [], [
            "Правила состава проекта (файлы, инструкции, .md) не применяются: проект не подключён (нет CONSTITUTION.md и нет .github/parch/)."
        ]
    found = {
        **file_violations(project),
        **instruction_violations(project),
        **markdown_violations(project),
        **features_violations(project),
        **(extra or {}),
    }
    debt = read_debt(project)
    problems: list[str] = []
    notes: list[str] = []
    basis = basis_problem(pr_body) if pr_body is not None else None
    if commits is not None:
        basis = commits_basis_problem(*commits)
    if basis is not None:
        if debt is None and existing_project(project):
            notes.append(f"Предупреждение (существующий проект, долг не записан): {basis}.")
        else:
            problems.append(f"Основания в PR: {basis}.")
    if commits is not None and changed is not None:
        protected = changed[1] or commits_protected_problem(commits[0], changed[0])
        if protected is not None:
            if debt is None and existing_project(project):
                notes.append(f"Предупреждение (существующий проект, долг не записан): {protected}.")
            else:
                problems.append(f"Защищённые файлы в PR: {protected}.")
    recording = (
        update and accept_new
    )  # владелец записывает долг: предупреждение не нужно, долг пишется ниже
    if debt is None and existing_project(project) and not recording:
        if found:
            notes.append(
                f"Предупреждение (проект записан как существующий, долг по составу не записан): нарушений {len(found)}. "
                "Запишите долг командой standard --update --accept-new, дальше новые нарушения будут падать."
            )
            notes += [f"  {m}" for m in list(found.values())[:20]]
        return problems, notes
    fresh = sorted(k for k in found if debt is None or k not in debt)
    if fresh and not accept_new:
        problems.append("Состав проекта не соответствует стандарту:")
        problems += [f"  {found[k]}" for k in fresh[:30]]
        if len(fresh) > 30:
            problems.append(f"  ... и ещё {len(fresh) - 30}")
        return problems, notes
    if update:
        new_debt = set(found) if accept_new else set(found) & (debt or set())
        if debt is None and accept_new or new_debt != (debt or set()):
            write_debt(project, new_debt)
            notes.append(f"Долг по составу проекта записан: нарушений {len(new_debt)}.")
    elif debt is not None and debt - set(found):
        notes.append(
            f"Исправлено нарушений из долга: {len(debt - set(found))}; сократите baseline: standard --update."
        )
    if found:
        notes.append(f"Нарушений состава в долге: {len(found)} (число только снижается).")
    return problems, notes


# ---------- карточка соответствия и версия стандарта (F14, PR 3; STANDARD.md, разделы 9 и 11) ----------

CARD = (
    ("P1", "Цель продукта записана и имеет проверяемые критерии"),
    ("P2", "Прослеживаемость цель, блоки, тесты, модули"),
    ("P3", "Инструкции для ИИ: одно место, короткие, без противоречий"),
    ("P4", "Инструкции Claude Project версионируются"),
    ("P5", "Расползание планов и заметок"),
    ("P6", "Решения записаны"),
    ("P7", "Защита от ложного «готово»"),
    ("P8", "Источник истины один"),
    ("P9", "Протокол перед решением"),
    ("P10", "Обработка сбоев"),
    ("P11", "Повторение ошибок"),
    ("P12", "Взаимодействие человек, Project, исполнитель"),
    ("P13", "Стоимость CI"),
)  # пункты раздела 9 стандарта: их же оценивает /parch:analyze-existing
ABSENT, PARTIAL, OK, ASK = "отсутствует", "частично", "по стандарту", "вопрос владельцу"
# Что изменилось в каждой версии (шапка docs/STANDARD.md) и какие пункты карточки это задевает.
# Последняя строка: действующая версия (та же, что STANDARD_VERSION в parch_ci.py, это проверяет тест).
STANDARD_CHANGES = (
    (
        "1.2",
        "защита от петли планировщика: бюджет инцидентов на блок, статус «застрял» (stuck), раздел «Влияние на цель» "
        "в отчётах об инцидентах, карточка решения для владельца на табло",
        ("P10", "P11", "P12"),
    ),
    (
        "1.3",
        "стоимость CI: Linux по умолчанию, таймаут и отмена устаревших прогонов, запуск только на PR, раннер не на "
        "Linux только через ADR с оценкой; целевая ОС записана в CONSTITUTION.md",
        ("P13",),
    ),
)
CURRENT_VERSION = STANDARD_CHANGES[-1][0]
VERSION_HEADING = "## Версия стандарта"
VERSION_NUMBER = re.compile(r"ProjectArchitect\s+(\d+(?:\.\d+)+)")


def grade_of(present: list[bool]) -> str:
    """Оценка пункта по признакам: все есть — по стандарту, часть — частично, ни одного — отсутствует."""
    if all(present):
        return OK
    return PARTIAL if any(present) else ABSENT


def first_text(project: Path, names: tuple[str, ...]) -> str:
    """Текст первого существующего файла из списка; пустая строка, если нет ни одного."""
    for name in names:
        path = project / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def version_key(version: str) -> tuple[int, ...]:
    """Версия «1.3» как (1, 3): так версии сравниваются по числам, а не по тексту."""
    return tuple(int(part) for part in version.split("."))


def project_standard_version(project: Path) -> str | None:
    """Версия из раздела «Версия стандарта» в CONSTITUTION.md (строка «ProjectArchitect 1.3»); None, если её нет."""
    _, heading, rest = first_text(project, CONSTITUTION_CANDIDATES).partition(VERSION_HEADING)
    if not heading:
        return None
    found = VERSION_NUMBER.search(rest.split("\n## ", 1)[0])
    return found.group(1) if found else None


def standard_changes(since: str) -> list[dict[str, object]]:
    """Изменения стандарта после версии `since`: версия, что изменилось, какие пункты карточки задеты."""
    return [
        {"version": version, "what": what, "points": list(points)}
        for version, what, points in STANDARD_CHANGES
        if version_key(version) > version_key(since)
    ]


def version_notes(project: Path) -> list[str]:
    """Что владельцу знать о версии стандарта проекта; пусто, если записана действующая версия."""
    own = project_standard_version(project)
    if own is None:
        return [
            f"В CONSTITUTION.md нет раздела «Версия стандарта» со строкой «ProjectArchitect {CURRENT_VERSION}»: "
            "без неё не видно, по какой версии правил проект приведён. Что доделать, покажет /parch:analyze-existing; "
            "строку вписывает владелец."
        ]
    if version_key(own) > version_key(CURRENT_VERSION):
        return [
            f"Проект записан на стандарт {own}, а установленные проверки знают только {CURRENT_VERSION}: "
            "обновите плагин parch, иначе новые правила не проверяются."
        ]
    changes = standard_changes(own)
    if not changes:
        return []
    lines = [f"Проект записан на стандарт {own}, действует {CURRENT_VERSION}. Что изменилось:"]
    for change in changes:
        points = ", ".join(cast("list[str]", change["points"]))
        lines.append(f"  {change['version']}: {change['what']} (пункты карточки: {points}).")
    lines.append(
        f"Что доделать, покажет /parch:analyze-existing; после приведения владелец меняет строку в CONSTITUTION.md "
        f"на «ProjectArchitect {CURRENT_VERSION}»."
    )
    return lines
