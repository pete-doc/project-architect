# ruff: noqa: E501, E402
"""Помощник для `/parch:analyze-existing`: факты о проекте, снимок инструментов, план приведения, проверка «ничего не менялось».

Вход: JSON-объект на stdin. Выход: JSON-объект на stdout. Анализ только читает проект; единственное, куда он пишет
файлы, это папка parch-analysis/ (и только командами baseline и write).

    {"command": "inventory", "project_dir": ".", "protected_paths": ["data/", "saves/"], "game_markers": ["steam.exe"]}
        `protected_paths`: папки и файлы; `game_markers`: слова, по которым швы с кодом игры попадают в группу (а). Факты, равноценные файлы, инструменты по языкам, горячие точки, кандидаты в мёртвый код, риски, план приведения
    {"command": "baseline", "project_dir": ".", "run": ["ruff", "pyright"], "protected_paths": [...], "allow_download": false}
        только если владелец разрешил: запуск перечисленных инструментов в режиме «только отчёт», результаты в
        parch-analysis/baseline/ (без списка «run» ничего не запускается); allow_download разрешает скачать jscpd,
        allow_build_steps разрешает собирать C#, если в проектах есть шаги после сборки
    {"command": "write", "project_dir": ".", "protected_paths": [...]}
        parch-analysis/: QUESTIONS.md, PLAN.md, drafts/GOAL.md, drafts/ADR-DRAFTS.md, facts.json
    {"command": "verify", "project_dir": "."}
        изменилось ли что-то в проекте, кроме parch-analysis/ (состояние «до» inventory сохраняет вне проекта)

Повторный анализ идёт в новую папку: любая команда принимает "report_dir" вида parch-analysis-2.
Папка analysis/ и любые другие папки проекта принадлежат владельцу и считаются кодом. Если parch-analysis/ уже есть
(и не создана этим анализом), inventory отказывается работать. Исходный документ: docs/design/analyze-existing.md.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_deadcode as deadcode
import analyze_equivalents as eqmod
import analyze_history as history
import analyze_map as mapmod
import analyze_plan as planmod
import analyze_report as report
import analyze_tools as tools

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "bin", "obj", "dist", "build", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vs",
}  # fmt: skip
MAX_FILES = 50_000
REPORT_DIR = "parch-analysis"  # единственное место, где анализу разрешено создавать файлы
MARKER = ".parch-analysis"
TEST_FILE = re.compile(r"(^|/)(tests?/|test_|.*\.(test|spec)\.|.*Tests?\.cs$)")
REPORT_NAME = re.compile(
    r"^parch-analysis[A-Za-z0-9_-]*$"
)  # повторный анализ идёт в новую папку: parch-analysis-2
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


def check_report_dir(name: str) -> str:
    if not REPORT_NAME.match(name):
        raise ValueError(
            f"имя папки отчёта {name!r} не подходит: нужно parch-analysis или parch-analysis-<слово>"
        )
    return name


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
                if item.name not in SKIP_DIRS and not item.name.startswith(REPORT_DIR):
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


def equivalent(facts: dict[str, Any], role: str) -> dict[str, Any] | None:
    for row in facts["equivalents"]:
        if row["role"] == role:
            return row
    return None


def card(facts: dict[str, Any], project: Path) -> list[dict[str, str]]:
    docs: dict[str, Any] = facts["docs"]
    flows: list[dict[str, Any]] = facts["workflows"]
    instructions: list[dict[str, Any]] = facts["instruction_files"]
    settings: dict[str, Any] = facts["claude_settings"]
    status: dict[str, tuple[str, str]] = {}
    goal_eq = equivalent(facts, "goal")
    if docs["goal_criteria"]:
        status["P1"] = (OK, f"GOAL.md есть, критериев: {docs['goal_criteria']}")
    elif docs["goal"]:
        status["P1"] = (PARTIAL, "GOAL.md есть, но критериев вида «G1.» в нём нет")
    elif goal_eq:
        status["P1"] = (
            PARTIAL,
            f"цель есть в {goal_eq['path']} (равноценен GOAL.md, засчитывается); критериев вида «G1.» нет",
        )
    else:
        status["P1"] = (ABSENT, "нет GOAL.md и равноценного файла цели")
    plan_eq, modules_eq = equivalent(facts, "plan"), equivalent(facts, "modules")
    have = [
        label for label, row in (("план блоков", plan_eq), ("карта модулей", modules_eq)) if row
    ]
    status["P2"] = (
        OK if len(have) == 2 else PARTIAL if have else ABSENT,
        "есть: "
        + (", ".join(have) if have else "ни плана блоков, ни карты модулей")
        + (
            "; равноценные файлы засчитаны"
            if any(r and not r["is_standard"] for r in (plan_eq, modules_eq))
            else ""
        ),
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
    decisions = equivalent(facts, "decisions")
    adr = docs["adr"]
    if decisions and not decisions["is_standard"]:
        files = docs["decisions_files"]
        status["P6"] = (
            OK if files >= 3 else PARTIAL,
            f"журнал решений в {decisions['path']} (равноценен ADR, засчитывается); "
            + (f"файлов записей: {files}" if files else "один файл без разбиения на записи"),
        )
    else:
        status["P6"] = (
            OK if adr >= 3 else PARTIAL if adr else ABSENT,
            f"записей решений (ADR): {adr}",
        )
    guards = [settings["hooks"], bool(flows), facts["git"]["is_repo"]]
    status["P7"] = (
        OK if all(guards) else PARTIAL if any(guards) else ABSENT,
        f"hooks: {'да' if settings['hooks'] else 'нет'}, CI: {'да' if flows else 'нет'}; защиту ветки по файлам не определить",
    )
    text = " ".join(read(project / i["path"]) for i in instructions).lower()
    status["P9"] = (
        PARTIAL if ("существующ" in text or "adr" in text) else ABSENT,
        (
            "в инструкциях есть упоминание существующего кода или решений"
            if ("существующ" in text or "adr" in text)
            else "в инструкциях нет требования искать существующий код и решения"
        )
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
    lessons = equivalent(facts, "lessons")
    status["P11"] = (
        PARTIAL if docs["lessons"] or lessons else ABSENT,
        "есть журнал уроков (связь с тестами проверяет агент)"
        if docs["lessons"] or lessons
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


def markdown_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    return len([p for p in path.rglob("*.md") if p.name.lower() != "readme.md"])


def inventory(
    project: Path,
    protected: list[str] | None = None,
    save_snapshot: bool = True,
    report_dir: str = REPORT_DIR,
    game_markers: list[str] | None = None,
) -> dict[str, Any]:
    """Факты о проекте. `save_snapshot=False` не трогает сохранённое состояние «до» (так зовёт его `write`)."""
    zones = planmod.clean_zone_list(protected or [])
    files = walk(project)
    rel = [f.relative_to(project).as_posix() for f in files]
    languages = {lang: sum(p.endswith(sfx) for p in rel) for lang, sfx in LANGUAGE_SUFFIXES.items()}
    instructions = instruction_facts(project)
    instruction_paths = {i["path"] for i in instructions}
    outside = [
        p for p in rel
        if p.endswith(".md") and not p.startswith("docs/") and not p.startswith(REPORT_DIR)
        and p not in instruction_paths and p.rsplit("/", 1)[-1] not in OK_MARKDOWN
    ]  # fmt: skip
    goal = project / "docs" / "GOAL.md"
    goal = goal if goal.is_file() else project / "GOAL.md"
    settings = project / ".claude" / "settings.json"
    settings_text = read(settings)
    adr_dir = project / "docs" / "adr"
    equivalents = eqmod.find_equivalents(project)
    incidents_path = eqmod.equivalent_path(equivalents, "incidents")
    decisions_path = eqmod.equivalent_path(equivalents, "decisions")
    status = snapshot(project)
    if status is not None and save_snapshot:
        snapshot_file(project).write_text(
            json.dumps(
                {
                    "project": str(project),
                    "snapshot": status,
                    "extra_allowed": [],
                    "report_dir": check_report_dir(report_dir),
                }
            ),
            encoding="utf-8",
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
        "test_files": sum(bool(TEST_FILE.search(p)) for p in rel),
        "test_files_by_language": dict(
            Counter(
                report.language_of(p) for p in rel if TEST_FILE.search(p) and report.language_of(p)
            )
        ),
        "workflows": workflow_facts(project),
        "instruction_files": instructions,
        "markdown_outside_docs": outside[:20],
        "markdown_outside_docs_total": len(outside),
        "equivalents": equivalents,
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
            "decisions_files": markdown_count(project / decisions_path)
            if decisions_path and decisions_path.endswith("/")
            else 0,
            "incidents": markdown_count(project / incidents_path) if incidents_path else 0,
        },
        "claude_settings": {
            "exists": settings.is_file(),
            "hooks": '"hooks"' in settings_text,
            "permissions": '"permissions"' in settings_text,
        },
    }
    facts["p_card"] = card(facts, project)
    facts["questions"] = [{"id": k, "question": v} for k, v in QUESTIONS.items()]
    facts["tools"] = tools.detect(project, rel, languages)
    facts["hotspots"] = history.hotspots(project, rel)
    facts["dead_code"] = deadcode.candidates(project, rel)
    facts["modules"] = mapmod.modules(project, rel, zones)
    facts["seams"] = mapmod.seams(project, rel, zones, game_markers)
    facts["outside_writes"] = mapmod.outside_writes(project, rel)
    auto_zones = sorted(facts["outside_writes"])  # файлы, пишущие вне проекта, для плана те же зоны
    facts["risks"] = report.risks(facts, facts["hotspots"], facts["dead_code"], facts["tools"])
    facts["health_card"] = report.health_card(
        languages,
        facts["tools"],
        facts["hotspots"],
        facts["dead_code"],
        [],
        facts["test_files_by_language"],
        facts["seams"],
    )
    plan_rows = planmod.build_plan(equivalents, rel, [*zones, *auto_zones], seams=facts["seams"])
    facts["protected_paths"] = zones
    facts["protected_files_auto"] = auto_zones
    facts["plan"] = plan_rows
    facts["plan_problems"] = planmod.validate(plan_rows, [*zones, *auto_zones])
    return facts


def report_dir_state(project: Path, report_dir: str = REPORT_DIR) -> Path:
    """Папка отчёта: новая или созданная этим анализом (с маркером). Чужую папку не трогаем."""
    folder = project / check_report_dir(report_dir)
    if folder.exists() and not (folder / MARKER).is_file():
        raise ValueError(
            f"папка {report_dir}/ уже существует и создана не этим анализом: остановись и спроси владельца"
        )
    folder.mkdir(exist_ok=True)
    (folder / MARKER).write_text("создано /parch:analyze-existing" + chr(10), encoding="utf-8")
    return folder


def baseline(
    project: Path,
    run: list[str],
    report_dir: str = REPORT_DIR,
    protected: list[str] | None = None,
    allow_download: bool = False,
    allow_build_steps: bool = False,
) -> dict[str, Any]:
    """Запуск разрешённых инструментов в режиме «только отчёт»; без списка ничего не запускается."""
    files = walk(project)
    names = [p.suffix.lower() for p in files]
    languages = {lang: sum(n in sfx for n in names) for lang, sfx in LANGUAGE_SUFFIXES.items()}
    folder = report_dir_state(project, report_dir)
    results = tools.run_baseline(
        project,
        run,
        languages,
        rel=[f.relative_to(project).as_posix() for f in files],
        protected=tuple(planmod.clean_zone_list(protected or [])),
        report_dir=folder,
        allow_download=allow_download,
        allow_build_steps=allow_build_steps,
    )
    written = tools.write_baseline(folder, results)
    if any(r["tool"].startswith("dotnet") and r.get("status") == "выполнен" for r in results):
        saved = snapshot_file(project)
        if saved.is_file():
            data = json.loads(saved.read_text(encoding="utf-8"))
            data["extra_allowed"] = list(tools.BUILD_DIRS)  # след разрешённой сборки C#
            saved.write_text(json.dumps(data), encoding="utf-8")
    return {
        "results": [{k: v for k, v in r.items() if k != "output_tail"} for r in results],
        "written": written,
    }


def load_baseline(folder: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in (
        sorted((folder / "baseline").glob("*.json")) if (folder / "baseline").is_dir() else []
    ):
        try:
            data: object = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            results.append(dict(data))  # pyright: ignore[reportUnknownArgumentType]
    return results


def write_artifacts(
    project: Path,
    protected: list[str] | None,
    report_dir: str = REPORT_DIR,
    game_markers: list[str] | None = None,
) -> dict[str, Any]:
    """Пишет в папку отчёта: QUESTIONS.md, PLAN.md, черновики GOAL и решений, facts.json."""
    facts = inventory(
        project, protected, save_snapshot=False, report_dir=report_dir, game_markers=game_markers
    )
    folder = report_dir_state(project, report_dir)
    results = load_baseline(folder)
    facts["baseline"] = [{k: v for k, v in r.items() if k != "output_tail"} for r in results]
    facts["health_card"] = report.health_card(
        facts["languages"],
        facts["tools"],
        facts["hotspots"],
        facts["dead_code"],
        results,
        facts["test_files_by_language"],
        facts["seams"],
    )
    written: list[str] = []

    def put(relative: str, text: str) -> None:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline=chr(10))
        written.append(f"{report_dir}/{relative}")

    put("QUESTIONS.md", deadcode.questions_markdown(facts["questions"], facts["dead_code"]))
    put(
        "PLAN.md",
        planmod.plan_markdown(
            facts["plan"], facts["protected_paths"], facts["outside_writes"], facts["seams"]
        ),
    )
    goal = report.goal_draft(project, facts["equivalents"])
    if goal:
        put("drafts/GOAL.md", goal)
    decisions = report.adr_drafts(project, facts["equivalents"])
    if decisions:
        put("drafts/ADR-DRAFTS.md", decisions)
    put("drafts/MODULES.md", mapmod.modules_markdown(facts["modules"], facts["outside_writes"]))
    put("drafts/INTERFACES.md", mapmod.interfaces_markdown(facts["seams"]))
    put("facts.json", json.dumps(facts, ensure_ascii=False, indent=2) + chr(10))
    return {
        "written": written,
        "plan_problems": facts["plan_problems"],
        "risks": len(facts["risks"]),
        "red_seams": sum(1 for s in facts["seams"] if s["red"]),
    }


def verify(project: Path, before: str | None, report_dir: str | None = None) -> dict[str, Any]:
    now = snapshot(project)
    saved = snapshot_file(project)
    extra: list[str] = []
    folder_name = report_dir
    if saved.is_file():
        saved_data = json.loads(saved.read_text(encoding="utf-8"))
        if saved_data["project"] == str(project):
            before = before if before is not None else saved_data["snapshot"]
            extra = list(saved_data.get("extra_allowed", []))
            folder_name = folder_name or saved_data.get("report_dir")
    folder_name = check_report_dir(folder_name or REPORT_DIR)
    if now is None:
        return {
            "ok": None,
            "changed": [],
            "note": "это не git-репозиторий: изменения проверить нельзя",
        }
    if before is None:
        raise ValueError("нет состояния «до»: сначала выполни inventory")
    old = set(before.splitlines())

    def allowed(line: str) -> bool:
        path = changed_path(line)
        return path.startswith(f"{folder_name}/") or any(part in extra for part in path.split("/"))

    changed = [
        line.split(chr(9))[0] for line in now.splitlines() if line not in old and not allowed(line)
    ]
    return {
        "ok": not changed,
        "changed": changed,
        "note": f"изменения вне {folder_name}/ найдены" if changed else "код не менялся",
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
        protected = request.get("protected_paths")
        markers = planmod.clean_zone_list(request.get("game_markers"))
        report_dir = check_report_dir(str(request.get("report_dir") or REPORT_DIR))
        if command == "inventory":
            if (project / report_dir).exists():
                raise ValueError(
                    f"папка {report_dir}/ уже существует: остановись и спроси владельца, как быть "
                    "(анализ не должен перезаписать или смешать её содержимое со своим отчётом)"
                )
            result = inventory(project, protected, report_dir=report_dir, game_markers=markers)
        elif command == "baseline":
            raw_run: Any = request.get("run") or []
            run = (
                [str(x) for x in cast("list[object]", raw_run)] if isinstance(raw_run, list) else []
            )
            result = baseline(
                project,
                run,
                report_dir,
                protected,
                allow_download=request.get("allow_download") is True,
                allow_build_steps=request.get("allow_build_steps") is True,
            )
        elif command == "write":
            result = write_artifacts(project, protected, report_dir, markers)
        elif command == "verify":
            result = verify(project, request.get("before"), request.get("report_dir"))
        else:
            raise ValueError("command должен быть inventory, baseline, write или verify")
    except (ValueError, OSError) as error:
        sys.stderr.write(f"analyze-existing: {error}\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
