#!/usr/bin/env python3
"""Генератор табло state/STATUS.md (STANDARD.md, раздел 7.1).

Табло строится только из `docs/GOAL.md`, `state/features.json`, отчёта о запуске тестов, файлов
`state/incidents/` и (необязательно) списка открытых PR. Своих данных у него нет, поэтому оно не
может разойтись с проектом. Статус «готово» берётся из тестов приёмки блока, а не из слов агентов.

    python parch_status.py --project . --report test-report.xml --commit SHA --out STATUS.md

Только стандартная библиотека (скрипт копируется в проекты рядом с parch_ci.py).
"""

# ruff: noqa: E501  (в строках лежат готовые русские фразы табло: переносить их нельзя без потери читаемости)
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast

STATUS_VIEW = {
    "planned": ("⏳", "впереди", "later"),
    "in_progress": ("🔨", "в работе", "work"),
    "blocked": ("⛔", "заблокирован", "blocked"),
    "stuck": ("🛑", "застрял", "blocked"),
    "waiting_owner": ("🙋", "ждёт владельца", "blocked"),
    "done": ("✅", "готово", "done"),
    "dropped": ("➖", "отказ", "later"),
}
CRITERION = re.compile(r"^- \*\*(G\d+)\.\*\*\s+(.+?)\s*$", re.MULTILINE)
# Утверждение: строка «Статус: утверждена владельцем, ГГГГ-ММ-ДД». Подсказка в черновике («ДАТА») не считается.
APPROVED = re.compile(r"Статус:\s*утверждена владельцем,\s*\d{4}-\d{2}-\d{2}")
GOAL_TITLE = re.compile(r"^# GOAL — цель продукта «(.+)»", re.MULTILINE)
ADR_FILE = re.compile(r"^\d{4}-.+\.md$")
INCIDENT_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<block>[A-Za-z0-9]+)-.+\.md$")
LABEL_LIMIT = 30


@dataclass
class Block:
    id: str
    title: str
    goals: list[str]
    depends_on: list[str]
    acceptance: list[str]
    status: str
    incidents: int = 0
    passes: bool = False
    shown: str = "planned"


@dataclass
class ReportSource:
    """Откуда отчёт тестов: коммит отчёта и совпадает ли его содержимое с текущим коммитом."""

    commit: str = ""
    same_tree: bool = False


@dataclass
class Board:
    name: str
    approved: bool
    criteria: dict[str, str]
    blocks: list[Block]
    problems: list[str] = field(default_factory=list[str])


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: ожидался объект JSON")
    return cast("dict[str, Any]", data)


def strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in cast("list[object]", value)]


def read_goal(project: Path) -> tuple[str, bool, dict[str, str]]:
    path = project / "docs" / "GOAL.md"
    if not path.is_file():
        return project.name, False, {}
    text = path.read_text(encoding="utf-8")
    title = GOAL_TITLE.search(text)
    approved = APPROVED.search(text) is not None
    return (title.group(1) if title else project.name), approved, dict(CRITERION.findall(text))


def read_blocks(project: Path, criteria: dict[str, str]) -> tuple[list[Block], list[str]]:
    path = project / "state" / "features.json"
    problems: list[str] = []
    if not path.is_file():
        return [], problems
    blocks: list[Block] = []
    for raw in cast("list[object]", read_json(path).get("features", [])):
        item = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        block = Block(
            id=str(item.get("id", "")).strip(),
            title=str(item.get("title", "")).strip(),
            goals=strings(item.get("goal")),
            depends_on=strings(item.get("depends_on")),
            acceptance=strings(item.get("acceptance_tests")),
            status=str(item.get("status", "planned")),
        )
        if not block.id:
            problems.append("в features.json есть блок без идентификатора (id)")
            continue
        if block.status not in STATUS_VIEW:
            problems.append(f"{block.id}: неизвестный статус «{block.status}»")
            block.status = "planned"
        if not block.goals or any(g not in criteria for g in block.goals):
            problems.append(
                f"{block.id}: нет цели или она ссылается на критерий, которого нет в GOAL.md"
            )
        blocks.append(block)
    return blocks, problems


def incident_counts(project: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    folder = project / "state" / "incidents"
    if folder.is_dir():
        for path in folder.iterdir():
            found = INCIDENT_FILE.match(path.name)
            if found:
                counts[found["block"]] = counts.get(found["block"], 0) + 1
    return counts


def matches(entry: str, key: str) -> bool:
    """Относится ли тест `key` из отчёта к файлу или классу приёмки `entry`."""
    head = key.split("::")[0]
    entry_path = PurePosixPath(entry.replace("\\", "/"))
    stem = entry_path.stem
    dotted = head.replace("/", ".")
    return (
        PurePosixPath(head).name == entry_path.name
        or dotted == stem
        or dotted.endswith("." + stem)
        or stem in re.split(r"[.(]", head)
    )


def block_passes(block: Block, outcomes: dict[str, str]) -> bool:
    """Блок прошёл, если у каждого теста приёмки есть запуски и все они «прошёл»."""
    if not block.acceptance:
        return False
    for entry in block.acceptance:
        states = [state for key, state in outcomes.items() if matches(entry, key)]
        if not states or any(state != "passed" for state in states):
            return False
    return True


def find_cycle(blocks: list[Block]) -> list[str]:
    graph = {b.id: [d for d in b.depends_on] for b in blocks}
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> list[str]:
        state[node] = 1
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return [*path, node, nxt]
            if state.get(nxt) is None and nxt in graph:
                found = visit(nxt, [*path, node])
                if found:
                    return found
        state[node] = 2
        return []

    for node in graph:
        if state.get(node) is None:
            found = visit(node, [])
            if found:
                return found
    return []


def compute(board: Board, outcomes: dict[str, str], incidents: dict[str, int]) -> None:
    by_id = {b.id: b for b in board.blocks}
    for block in board.blocks:
        block.incidents = incidents.get(block.id, 0)
        block.passes = block_passes(block, outcomes)
        for dep in block.depends_on:
            if dep not in by_id:
                board.problems.append(f"{block.id}: зависит от несуществующего блока {dep}")
    cycle = find_cycle(board.blocks)
    if cycle:
        board.problems.append("цикл зависимостей между блоками: " + " → ".join(cycle))
    for block in board.blocks:
        block.shown = block.status
        if block.status in {"blocked", "stuck"} and block.incidents == 0:
            board.problems.append(
                f"{block.id}: статус «{STATUS_VIEW[block.status][1]}» без отчёта в state/incidents/ "
                "— нарушение стандарта: причину блокировки нужно записать"
            )
        if block.status == "done" and not block.passes:
            board.problems.append(
                f"{block.id}: помечен «готово», но тесты приёмки не прошли или не найдены в отчёте"
            )
            block.shown = "in_progress"
    changed = True
    while changed:  # «готово» только если готовы и все блоки, от которых он зависит
        changed = False
        for block in board.blocks:
            if block.shown == "done" and any(
                by_id[d].shown != "done" for d in block.depends_on if d in by_id
            ):
                board.problems.append(f"{block.id}: «готово», но зависит от неготового блока")
                block.shown = "in_progress"
                changed = True
    for (
        block
    ) in board.blocks:  # «готово» ставит только отчёт CI: тесты приёмки прошли, зависимости готовы
        if block.status in {"planned", "in_progress"} and block.passes:
            if all(by_id[d].shown == "done" for d in block.depends_on if d in by_id):
                block.shown = "done"


# ---------- вывод ----------


def read_outcomes(report: Path | None) -> dict[str, str]:
    if report is None:
        return {}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import parch_ci

    return parch_ci.any_report_outcomes(report)


def adr_waiting(project: Path) -> list[str]:
    found: list[str] = []
    folder = project / "docs" / "adr"
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not ADR_FILE.match(path.name) or path.name.startswith("0000"):
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^- Статус:\s*proposed\b", text, re.MULTILINE | re.IGNORECASE):
            title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
            found.append(f"{title} — ждёт утверждения владельца")
    return found


def open_questions(project: Path) -> list[str]:
    path = project / "docs" / "QUESTIONS.md"
    if not path.is_file():
        return []
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 5 and cells[0].isdigit() and not cells[4]:
            found.append(f"Вопрос {cells[0]}: {cells[1]}")
    return found


def short(text: str, limit: int = LABEL_LIMIT) -> str:
    text = text.replace('"', "'").replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def diagram(board: Board, goal: str) -> str:
    mine = [b for b in board.blocks if goal in b.goals]
    by_id = {b.id: b for b in board.blocks}
    context = [
        by_id[d] for b in mine for d in b.depends_on if d in by_id and goal not in by_id[d].goals
    ]
    shown = {b.id: b for b in [*mine, *context]}
    lines = ["graph LR"]
    lines += [f'  {b.id}["{b.id} {short(b.title)}"]' for b in shown.values()]
    lines += [f"  {d} --> {b.id}" for b in mine for d in b.depends_on if d in shown]
    lines += [
        "  classDef done fill:#d4f4dd,stroke:#2e8b57",
        "  classDef work fill:#fff3c4,stroke:#c79100",
        "  classDef blocked fill:#ffd6d6,stroke:#c0392b",
        "  classDef later fill:#eeeeee,stroke:#999999",
        "  classDef outside stroke-dasharray: 4 3",
    ]
    for css in ("done", "work", "blocked", "later"):
        ids = [i for i, b in shown.items() if STATUS_VIEW[b.shown][2] == css]
        if ids:
            lines.append(f"  class {','.join(ids)} {css}")
    outside = list(dict.fromkeys(b.id for b in context))
    if outside:
        lines.append(f"  class {','.join(outside)} outside")
    return "\n".join(lines)


def headline(board: Board) -> str:
    done = sum(b.shown == "done" for b in board.blocks)
    parts = [f"{done} из {len(board.blocks)} блоков готово"]
    for goal in board.criteria:
        mine = [b for b in board.blocks if goal in b.goals]
        ready = sum(b.shown == "done" for b in mine)
        if not mine:
            parts.append(f"{goal} без блоков")
        elif ready == len(mine):
            parts.append(f"критерий {goal} выполнен")
        elif ready == 0 and all(b.shown == "planned" for b in mine):
            parts.append(f"{goal} не начат")
        else:
            parts.append(f"{goal} — {ready} из {len(mine)}")
    return " · ".join(parts)


def render(
    board: Board,
    project: Path,
    outcomes: dict[str, str],
    commit: str,
    date: str,
    previous: int | None,
    prs: list[dict[str, Any]],
    source: ReportSource,
    ci_runs: list[dict[str, Any]] | None = None,
) -> str:
    out = [f"# Прогресс: {headline(board) if board.blocks else 'блоков пока нет'}"]
    out.append(
        f"Обновлено: {date}, коммит `{commit}` · данные: `state/features.json`, `docs/GOAL.md`, отчёт CI, `state/incidents/`"
    )
    stale = report_warning(outcomes, source, commit)
    if stale:
        out += ["", f"> ⚠ {stale}"]
    out.append("")
    decisions: list[str] = []
    if not board.approved:
        decisions.append(
            "**Цель `docs/GOAL.md` ещё не утверждена** (черновик): без утверждённой цели планирование блоков не начинается"
        )
    decisions += [
        f"**PR #{p['number']}** «{p['title']}» — ждёт решения владельца"
        for p in prs
        if "нужно решение владельца" in cast("list[str]", p.get("labels", []))
    ]
    decisions += adr_waiting(project)
    decisions += [
        f"**{b.id} «{b.title}»** {STATUS_VIEW[b.shown][1]}"
        + (f" (инцидентов: {b.incidents})" if b.incidents else "")
        for b in board.blocks
        if b.shown in {"waiting_owner", "stuck"}
    ]
    decisions += open_questions(project)
    out += (
        ["## Нужно ваше решение"]
        + ([f"- {x}" for x in decisions] or ["- ничего: всё идёт без вашего участия"])
        + [""]
    )
    working = [f"- **PR #{p['number']}** «{p['title']}» — {checks_text(p)}" for p in prs]
    working += [
        f"- {b.id} «{b.title}» — идёт работа" for b in board.blocks if b.shown == "in_progress"
    ]
    working += [
        f"- {b.id} «{b.title}» — заблокирован (инцидентов: {b.incidents}), причина в `state/incidents/`"
        for b in board.blocks
        if b.shown == "blocked"
    ]
    out += ["## В работе"] + (working or ["- ничего не открыто"]) + [""]
    if board.blocks:
        out += plan_table(board) + [""]
        out += [
            "## Зависимости блоков",
            "По одной небольшой диаграмме на критерий готовности. Пунктирные блоки относятся к другому критерию и показаны только как «от чего зависит».",
            "",
        ]
        for goal, text in board.criteria.items():
            mine = [b for b in board.blocks if goal in b.goals]
            if mine:
                ready = sum(b.shown == "done" for b in mine)
                out += [
                    f"### {goal} {text}: {ready} из {len(mine)} блоков готово",
                    "```mermaid",
                    diagram(board, goal),
                    "```",
                    "",
                ]
    else:
        out += [
            "## План",
            "Блоков пока нет. Планировщик заводит их в `state/features.json` после утверждения цели.",
            "",
        ]
    out += tests_section(outcomes, previous, source, commit)
    out += ci_section(ci_runs, date)
    if board.problems:
        out += ["## Замечания к плану"] + [f"- {x}" for x in board.problems] + [""]
    out += [
        "## Соответствие стандарту",
        "Карточка пунктов P1–P13 появится вместе с полной проверкой `standard`.",
        "",
    ]
    return "\n".join(out)


def normalize_pr(item: dict[str, Any]) -> dict[str, Any]:
    """PR из `gh pr list --json number,title,isDraft,labels,statusCheckRollup` или уже готовый."""
    labels: list[str] = []
    for entry in cast("list[Any]", item.get("labels", [])):
        labels.append(
            str(cast("dict[str, Any]", entry).get("name", ""))
            if isinstance(entry, dict)
            else str(entry)
        )
    checks = item.get("checks")
    if checks is None:
        rollup = cast("list[dict[str, Any]]", item.get("statusCheckRollup") or [])
        if any(
            str(c.get("conclusion", "")).upper() in {"FAILURE", "TIMED_OUT", "CANCELLED"}
            for c in rollup
        ):
            checks = "FAILURE"
        elif any(str(c.get("status", "")).upper() != "COMPLETED" for c in rollup):
            checks = "PENDING"
        else:
            checks = "SUCCESS" if rollup else "NONE"
    return {**item, "labels": labels, "checks": checks}


def checks_text(pr: dict[str, Any]) -> str:
    state = {"SUCCESS": "CI зелёный", "FAILURE": "CI красный", "PENDING": "CI идёт"}.get(
        str(pr.get("checks", "")), "CI нет данных"
    )
    return state + (", черновик" if pr.get("isDraft") else "")


def plan_table(board: Board) -> list[str]:
    rows = [
        "## План",
        "| Критерий | Блок | Что это | Статус | Зависит от | Инциденты |",
        "|---|---|---|---|---|---|",
    ]
    ordered = sorted(
        board.blocks, key=lambda b: ((b.goals or ["~"])[0], int(re.sub(r"\D", "", b.id) or 0))
    )
    for b in ordered:
        icon, word, _ = STATUS_VIEW[b.shown]
        goals = ", ".join(f"{g} {short(board.criteria.get(g, '?'), 45)}" for g in b.goals) or "—"
        rows.append(
            f"| {goals} | {b.id} | {b.title} | {icon} {word} | {', '.join(b.depends_on) or '—'} | {b.incidents} |"
        )
    return rows


BILLING_LINKS = (
    "Минуты и деньги смотрите на GitHub: [использование Actions](https://github.com/settings/billing/summary), "
    "[бюджеты](https://github.com/settings/billing/budgets)."
)
CI_WEEK_DAYS = 7
CI_TABLE_ROWS = 8
RUNNER_MULTIPLIER = (("windows", 2), ("macos", 10))  # как GitHub считает квоту: Linux x1
WASTED = {"failure", "cancelled", "timed_out"}


def parse_time(value: object) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def run_minutes(run: dict[str, Any]) -> int:
    """Минуты квоты одного прогона: каждое задание округляется вверх, Windows x2, macOS x10."""
    total = 0
    for raw in cast("list[object]", run.get("jobs", [])):
        job = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        start, end = parse_time(job.get("started_at")), parse_time(job.get("completed_at"))
        if start is None or end is None or end < start:
            continue
        labels = " ".join(str(x).lower() for x in cast("list[object]", job.get("labels", [])))
        factor = next((f for key, f in RUNNER_MULTIPLIER if key in labels), 1)
        total += -(-int((end - start).total_seconds()) // 60) * factor
    return total


def ci_section(runs: list[dict[str, Any]] | None, date: str) -> list[str]:
    """Раздел «Расход CI»: минуты за неделю и на один PR по данным о прогонах Actions."""
    if runs is None:
        return ["## Расход CI", BILLING_LINKS, ""]
    end = datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.UTC) + datetime.timedelta(
        days=1
    )
    since = end - datetime.timedelta(days=CI_WEEK_DAYS)
    week: list[tuple[dict[str, Any], int]] = []
    for run in runs:
        created = parse_time(run.get("created"))
        if created is not None and since <= created < end:
            week.append((run, run_minutes(run)))
    total = sum(m for _, m in week)
    wasted = [(r, m) for r, m in week if r.get("conclusion") in WASTED]
    out = [
        "## Расход CI",
        f"- За {CI_WEEK_DAYS} дней: **{total}** минут квоты в {len(week)} прогонах "
        f"(Windows считается x2). Впустую, то есть упало или отменено: **{sum(m for _, m in wasted)}** "
        f"минут в {len(wasted)} прогонах.",
    ]
    per_pr: dict[int, list[int]] = {}
    for run, minutes in week:
        number = run.get("pr")
        if isinstance(number, int):
            row = per_pr.setdefault(number, [0, 0, 0])
            row[0] += 1
            row[1] += minutes
            row[2] += run.get("conclusion") in WASTED
    other = sum(m for r, m in week if not isinstance(r.get("pr"), int))
    if per_pr:
        average = round(sum(r[1] for r in per_pr.values()) / len(per_pr))
        worst = max(per_pr, key=lambda n: per_pr[n][1])
        out.append(
            f"- На один PR: в среднем **{average}** минут (PR за неделю: {len(per_pr)}); больше всего у "
            f"PR #{worst}: {per_pr[worst][1]} минут."
        )
        out += ["", "| PR | Прогонов | Минут | Из них впустую |", "|---|---|---|---|"]
        for number in sorted(per_pr, reverse=True)[:CI_TABLE_ROWS]:
            count, minutes, bad = per_pr[number]
            out.append(f"| #{number} | {count} | {minutes} | {bad} |")
        out.append("")
    else:
        out.append("- На один PR: за неделю PR с прогонами не было.")
    out.append(f"- Вне PR (после слияния, табло): {other} минут.")
    return [*out, BILLING_LINKS, ""]


def report_warning(outcomes: dict[str, str], source: ReportSource, commit: str) -> str:
    """Предупреждение, если отчёт тестов снят не с содержимого текущего коммита ("" = всё в порядке)."""
    if not outcomes:
        return ""
    if not source.commit:
        return (
            "**Отчёт тестов снят неизвестно когда:** коммит отчёта не указан, "
            "готовность блоков может не соответствовать коду."
        )
    if source.same_tree:
        return ""
    return (
        f"**Отчёт тестов снят не с текущего коммита:** отчёт с `{source.commit}`, табло на `{commit}`. "
        "Готовность блоков и счётчики тестов могут отставать от кода."
    )


def tests_section(
    outcomes: dict[str, str], previous: int | None, source: ReportSource, commit: str
) -> list[str]:
    if not outcomes:
        return ["## Тесты", "Отчёта о запуске тестов пока нет.", ""]
    total = len(outcomes)
    failed = sorted(k for k, v in outcomes.items() if v == "failed")
    skipped = sorted(k for k, v in outcomes.items() if v == "skipped")
    line = (
        f"- Всего **{total}**"
        + (f" (было {previous})" if previous is not None and previous != total else "")
        + "."
    )
    out = ["## Тесты", line, f"- Упавших: **{len(failed)}**. Пропущенных: **{len(skipped)}**."]
    out += [f"  - упал: {k}" for k in failed[:10]]
    where = f"отчёт прогона CI на коммите `{source.commit}`" if source.commit else "отчёт CI"
    if report_warning(outcomes, source, commit):
        origin = f"- Источник: {where}. ⚠ Это не текущий коммит `{commit}`: данные могли устареть."
    else:
        origin = f"- Источник: {where} (то же содержимое, что у табло)."
    return [*out, origin, ""]


def previous_total(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    found = re.search(r"Всего \*\*(\d+)\*\*", path.read_text(encoding="utf-8").replace(" ", ""))
    return int(found.group(1)) if found else None


def build(
    project: Path,
    report: Path | None,
    commit: str,
    date: str,
    previous: Path | None,
    prs: list[dict[str, Any]],
    source: ReportSource | None = None,
    ci_runs: list[dict[str, Any]] | None = None,
) -> str:
    name, approved, criteria = read_goal(project)
    blocks, problems = read_blocks(project, criteria)
    board = Board(name, approved, criteria, blocks, problems)
    outcomes = read_outcomes(report)
    compute(board, outcomes, incident_counts(project))
    return render(
        board, project, outcomes, commit, date, previous_total(previous), prs,
        source or ReportSource(), ci_runs,
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="parch_status")
    parser.add_argument("--project", default=".")
    parser.add_argument("--report", default=None)
    parser.add_argument("--commit", default="неизвестен")
    parser.add_argument("--date", default=None)
    parser.add_argument(
        "--previous", default=None, help="прежний STATUS.md: из него берётся число тестов"
    )
    parser.add_argument("--report-commit", default="", help="коммит, с которого снят отчёт тестов")
    parser.add_argument(
        "--report-same-tree",
        choices=["yes", "no"],
        default="no",
        help="yes, если содержимое коммита отчёта совпадает с текущим коммитом",
    )
    parser.add_argument(
        "--ci-runs", default=None, help="JSON с прогонами Actions за неделю: расход CI на табло"
    )
    parser.add_argument("--prs", default=None, help="JSON со списком открытых PR")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    date = args.date or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    prs: list[dict[str, Any]] = []
    if args.prs:
        raw = cast("list[dict[str, Any]]", json.loads(Path(args.prs).read_text(encoding="utf-8")))
        prs = [normalize_pr(item) for item in raw]
    ci_runs: list[dict[str, Any]] | None = None
    if args.ci_runs and Path(args.ci_runs).is_file():
        ci_runs = cast(
            "list[dict[str, Any]]", json.loads(Path(args.ci_runs).read_text(encoding="utf-8"))
        )
    try:
        text = build(
            project, Path(args.report) if args.report else None, args.commit, date,
            Path(args.previous) if args.previous else None, prs,
            ReportSource(args.report_commit, args.report_same_tree == "yes"),
            ci_runs,
        )  # fmt: skip
    except (ValueError, OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"[parch:status] ПРОВАЛ: {error}\n")
        return 1
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
