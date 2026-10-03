# ruff: noqa: E501
"""Помощник для `/parch:analyze-existing` (упрощённая версия): факты о проекте и проверка «ничего не менялось».

Вход: JSON-объект на stdin. Выход: JSON-объект на stdout. Скрипт только читает проект: он ничего в нём не создаёт и не меняет.

    {"command": "inventory", "project_dir": "."}   факты и предварительная карточка пунктов P1-P13
    {"command": "verify", "project_dir": "."}      изменилось ли что-то в проекте, кроме parch-analysis/
                                                   (состояние «до» inventory сохраняет вне проекта)

Отчёт пишется только в папку parch-analysis/. Если она уже есть, inventory отказывается работать:
папка analysis/ и любые другие папки проекта принадлежат владельцу и считаются кодом.

Оценки пунктов предварительные: по ним агент пишет отчёт, а пункты, которые по файлам не определить
(P4, P8, P12), остаются вопросами владельцу.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "bin", "obj", "dist", "build", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vs",
}  # fmt: skip
MAX_FILES = 50_000
REPORT_DIR = "parch-analysis"  # единственное место, где анализу разрешено создавать файлы
LANGUAGE_SUFFIXES = {
    "python": (".py",),
    "typescript": (".ts", ".tsx"),
    "csharp": (".cs",),
    "powershell": (".ps1", ".psm1"),
}
INSTRUCTION_FILES = (
    "CLAUDE.md", "AGENTS.md", ".cursorrules", ".github/copilot-instructions.md", "GEMINI.md",
)  # fmt: skip
INSTRUCTION_DIRS = (".cursor/rules",)
OK_MARKDOWN = {"README.md", "CHANGELOG.md", "LICENSE.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md"}
AGENTS_MAX_LINES = 150
GOAL_CRITERION = re.compile(r"^- \*\*G\d+\.\*\*", re.MULTILINE)
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
)
QUESTIONS = {
    "P4": "Где хранится канонический текст инструкций Claude Project и совпадает ли он с тем, что показан в настройках Project?",
    "P8": "Где живёт состояние проекта: репозиторий, внешние трекеры, чаты, файлы на диске? Есть ли расхождения?",
    "P12": "Кто что решает и через что передаёт: есть ли решения, которые существуют только в чатах?",
}  # fmt: skip
ABSENT, PARTIAL, OK, ASK = "отсутствует", "частично", "по стандарту", "вопрос владельцу"


def git(project: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def digest(path: Path) -> str:
    """Отпечаток содержимого файла: правка уже изменённого или нового файла видна в состоянии «до/после»."""
    if not path.is_file():
        return "-"
    sha = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def changed_path(line: str) -> str:
    """Путь из строки состояния «XY путь<TAB>отпечаток» (git берёт пути с пробелами в кавычки)."""
    path = line.split(chr(9))[0][3:].split(" -> ")[-1].strip('"')
    return path.replace(chr(92), "/")


def snapshot(project: Path) -> str | None:
    """Состояние дерева по git: изменённые и новые файлы с отпечатками (None, если это не git-репозиторий)."""
    status = git(project, "-c", "core.quotepath=off", "status", "--porcelain=v1", "-uall")
    if status is None:
        return None
    return chr(10).join(
        f"{line}{chr(9)}{digest(project / changed_path(line))}" for line in status.splitlines()
    )


def snapshot_file(project: Path) -> Path:
    """Где inventory сохраняет состояние «до»: вне проекта, чтобы в проекте ничего не появлялось."""
    key = hashlib.sha1(str(project).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"parch-analyze-{key}.json"


def walk(project: Path) -> list[Path]:
    found: list[Path] = []
    stack = [project]
    while stack and len(found) < MAX_FILES:
        for item in sorted(stack.pop().iterdir()):
            if item.is_dir():
                if item.name not in SKIP_DIRS:
                    stack.append(item)
            else:
                found.append(item)
    return found


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def workflow_facts(project: Path) -> list[dict[str, Any]]:
    folder = project / ".github" / "workflows"
    facts: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
        text = read(path)
        on_block = re.search(r"^on:\s*(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
        head = on_block.group(1) if on_block else ""
        triggers = sorted(
            {t for t in ("push", "pull_request", "schedule", "workflow_dispatch") if t in head}
        )
        runners = sorted(set(re.findall(r"runs-on:\s*([^\s#]+)", text)))
        facts.append(
            {
                "file": path.name,
                "timeout": text.count("timeout-minutes:") >= text.count("runs-on:") > 0,
                "concurrency": "concurrency:" in text,
                "triggers": triggers,
                "runners": runners,
            }
        )
    return facts


def instruction_facts(project: Path) -> list[dict[str, Any]]:
    paths = [project / name for name in INSTRUCTION_FILES]
    for folder in INSTRUCTION_DIRS:
        directory = project / folder
        paths += (
            sorted(p for p in directory.rglob("*") if p.is_file()) if directory.is_dir() else []
        )
    return [
        {"path": p.relative_to(project).as_posix(), "lines": len(read(p).splitlines())}
        for p in paths
        if p.is_file()
    ]


def card(facts: dict[str, Any], project: Path) -> list[dict[str, str]]:
    docs: dict[str, Any] = facts["docs"]
    flows: list[dict[str, Any]] = facts["workflows"]
    instructions: list[dict[str, Any]] = facts["instruction_files"]
    settings: dict[str, Any] = facts["claude_settings"]
    status: dict[str, tuple[str, str]] = {}
    if docs["goal_criteria"]:
        status["P1"] = (OK, f"GOAL.md есть, критериев: {docs['goal_criteria']}")
    elif docs["goal"]:
        status["P1"] = (PARTIAL, "GOAL.md есть, но критериев вида «G1.» в нём нет")
    else:
        status["P1"] = (ABSENT, "нет GOAL.md")
    have = [name for name in ("features", "modules") if docs[name]]
    status["P2"] = (
        OK if len(have) == 2 else PARTIAL if have else ABSENT,
        "есть: " + (", ".join(have) if have else "ни плана блоков, ни карты модулей"),
    )
    long = [i["path"] for i in instructions if i["lines"] > AGENTS_MAX_LINES]
    if not instructions:
        status["P3"] = (ABSENT, "файлов инструкций для ИИ нет")
    elif len(instructions) == 1 and not long:
        status["P3"] = (
            OK,
            f"один файл {instructions[0]['path']}, строк: {instructions[0]['lines']}",
        )
    else:
        why = f"файлов инструкций: {len(instructions)}" + (
            f"; длиннее {AGENTS_MAX_LINES} строк: {', '.join(long)}" if long else ""
        )
        status["P3"] = (PARTIAL, why + "; противоречия между ними проверяет агент по тексту")
    outside = facts["markdown_outside_docs_total"]
    status["P5"] = (
        OK if outside <= 3 else PARTIAL if outside <= 10 else ABSENT,
        f"заметок и планов вне docs/: {outside}",
    )
    adr = docs["adr"]
    status["P6"] = (OK if adr >= 3 else PARTIAL if adr else ABSENT, f"записей решений (ADR): {adr}")
    guards = [settings["hooks"], bool(flows), facts["git"]["is_repo"]]
    status["P7"] = (
        OK if all(guards) else PARTIAL if any(guards) else ABSENT,
        f"hooks: {'да' if settings['hooks'] else 'нет'}, CI: {'да' if flows else 'нет'}; защиту ветки по файлам не определить",
    )
    text = " ".join(read(project / i["path"]) for i in instructions).lower()
    status["P9"] = (
        PARTIAL if ("существующ" in text or "adr" in text) else ABSENT,
        "в инструкциях есть упоминание существующего кода или решений"
        if text
        else "инструкций нет",
    )
    budget = (
        "бюджет"
        in read(project / "CONSTITUTION.md").lower()
        + read(project / "docs" / "CONSTITUTION.md").lower()
    )
    status["P10"] = (
        OK if docs["incidents"] and budget else PARTIAL if docs["incidents"] or budget else ABSENT,
        f"отчётов об инцидентах: {docs['incidents']}, бюджет в конституции: {'да' if budget else 'нет'}",
    )
    status["P11"] = (
        PARTIAL if docs["lessons"] else ABSENT,
        "есть LESSONS.md (связь с тестами проверяет агент)"
        if docs["lessons"]
        else "нет LESSONS.md",
    )
    if not flows:
        status["P13"] = (ABSENT, "нет workflow GitHub Actions")
    else:
        bad = [
            f["file"] for f in flows
            if not f["timeout"] or not f["concurrency"] or "push" in f["triggers"]
            or any("ubuntu" not in r for r in f["runners"])
        ]  # fmt: skip
        status["P13"] = (
            PARTIAL if bad else OK,
            ("замечания к: " + ", ".join(bad)) if bad else "таймауты, отмена, запуск по PR, Linux",
        )
    for key, question in QUESTIONS.items():
        status[key] = (ASK, question)
    return [{"id": i, "title": t, "status": status[i][0], "why": status[i][1]} for i, t in CARD]


def inventory(project: Path) -> dict[str, Any]:
    files = walk(project)
    rel = [f.relative_to(project).as_posix() for f in files]
    languages = {lang: sum(p.endswith(sfx) for p in rel) for lang, sfx in LANGUAGE_SUFFIXES.items()}
    instructions = instruction_facts(project)
    instruction_paths = {i["path"] for i in instructions}
    outside = [
        p for p in rel
        if p.endswith(".md") and not p.startswith("docs/") and not p.startswith(f"{REPORT_DIR}/")
        and p not in instruction_paths and p.rsplit("/", 1)[-1] not in OK_MARKDOWN
    ]  # fmt: skip
    goal = project / "docs" / "GOAL.md"
    goal = goal if goal.is_file() else project / "GOAL.md"
    settings = project / ".claude" / "settings.json"
    settings_text = read(settings)
    adr_dir = project / "docs" / "adr"
    incidents = project / "state" / "incidents"
    status = snapshot(project)
    if status is not None:
        snapshot_file(project).write_text(
            json.dumps({"project": str(project), "snapshot": status}), encoding="utf-8"
        )
    facts: dict[str, Any] = {
        "project": project.name,
        "git": {
            "is_repo": status is not None,
            "branch": (git(project, "branch", "--show-current") or "").strip() or None,
            "clean": (status == "") if status is not None else None,
            "dirty_files": len(status.splitlines()) if status else 0,
            "snapshot_saved": status is not None,
        },
        "languages": languages,
        "test_files": sum(
            bool(re.search(r"(^|/)(tests?/|test_|.*\.(test|spec)\.|.*Tests?\.cs$)", p)) for p in rel
        ),
        "workflows": workflow_facts(project),
        "instruction_files": instructions,
        "markdown_outside_docs": outside[:20],
        "markdown_outside_docs_total": len(outside),
        "docs": {
            "goal": goal.is_file(),
            "goal_criteria": len(GOAL_CRITERION.findall(read(goal))),
            "constitution": (project / "docs" / "CONSTITUTION.md").is_file()
            or (project / "CONSTITUTION.md").is_file(),
            "modules": (project / "docs" / "MODULES.md").is_file(),
            "questions": (project / "docs" / "QUESTIONS.md").is_file(),
            "lessons": (project / "docs" / "LESSONS.md").is_file()
            or (project / "LESSONS.md").is_file(),
            "features": (project / "state" / "features.json").is_file(),
            "project_instructions": (project / "docs" / "PROJECT_INSTRUCTIONS.md").is_file(),
            "adr": len([p for p in adr_dir.glob("*.md") if p.name[:4].isdigit()])
            if adr_dir.is_dir()
            else 0,
            "incidents": len(list(incidents.glob("*.md"))) if incidents.is_dir() else 0,
        },
        "claude_settings": {
            "exists": settings.is_file(),
            "hooks": '"hooks"' in settings_text,
            "permissions": '"permissions"' in settings_text,
        },
    }
    facts["p_card"] = card(facts, project)
    facts["questions"] = [{"id": k, "question": v} for k, v in QUESTIONS.items()]
    return facts


def verify(project: Path, before: str | None) -> dict[str, Any]:
    now = snapshot(project)
    saved = snapshot_file(project)
    if before is None and saved.is_file():
        saved_data = json.loads(saved.read_text(encoding="utf-8"))
        before = saved_data["snapshot"] if saved_data["project"] == str(project) else None
    if now is None:
        return {
            "ok": None,
            "changed": [],
            "note": "это не git-репозиторий: изменения проверить нельзя",
        }
    if before is None:
        raise ValueError("нет состояния «до»: сначала выполни inventory")
    old = set(before.splitlines())
    changed = [
        line.split(chr(9))[0]
        for line in now.splitlines()
        if line not in old and not changed_path(line).startswith(f"{REPORT_DIR}/")
    ]
    return {
        "ok": not changed,
        "changed": changed,
        "note": f"изменения вне {REPORT_DIR}/ найдены" if changed else "код не менялся",
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    try:
        request = json.loads(sys.stdin.read() or "{}")
        project = Path(str(request.get("project_dir", "."))).resolve()
        command = request.get("command")
        if not project.is_dir():
            raise ValueError(f"нет папки проекта: {project}")
        if command == "inventory":
            if (project / REPORT_DIR).exists():
                raise ValueError(
                    f"папка {REPORT_DIR}/ уже существует: остановись и спроси владельца, как быть "
                    "(анализ не должен перезаписать или смешать её содержимое со своим отчётом)"
                )
            result = inventory(project)
        elif command == "verify":
            result = verify(project, request.get("before"))
        else:
            raise ValueError("command должен быть inventory или verify")
    except (ValueError, OSError) as error:
        sys.stderr.write(f"analyze-existing: {error}\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
