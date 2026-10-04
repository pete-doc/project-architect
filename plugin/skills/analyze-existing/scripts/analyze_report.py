# ruff: noqa: E501
"""Риски, карточка здоровья и черновики для отчёта (docs/design/analyze-existing.md, шаг 5 и «Карточка здоровья»).

Всё детерминированно и читает только то, что уже собрал inventory; тексты для владельца пишет агент по этим данным.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_RISKS = 10
MIN_RISKS = 5
DRAFT_MARK = "черновик, ждёт утверждения владельца"
HEALTH_SIGNALS = (
    ("build", "Сборка и тесты проходят", "нет"),
    ("tests", "Число тестов и покрытие", "тестов почти нет, особенно в горячих точках"),
    ("lint", "Ошибки типов и линтера", "сотни на тысячу строк"),
    ("architecture", "Нарушения архитектуры", "циклические зависимости между модулями"),
    ("dead", "Мёртвый код", "заметная доля кода не используется"),
    ("dup", "Дублирование", "процент выше примерно 5–10%"),
    ("seams", "Межъязыковые швы без тестов", "любой шов без контрактного теста"),
    ("hotspots", "Горячие точки", "большие файлы, которые часто меняются и без тестов"),
)


def health_card(
    languages: dict[str, int],
    tools: dict[str, list[dict[str, Any]]],
    hot: list[dict[str, Any]],
    dead: dict[str, Any],
    baseline: list[dict[str, Any]],
    test_files: int,
) -> list[dict[str, Any]]:
    """Карточка здоровья по языкам. Чего не измеряли, так и пишем: «не измерялось»."""
    ran = {str(b["tool"]): b for b in baseline if b.get("status") == "выполнен"}
    rows: list[dict[str, Any]] = []
    for language, count in languages.items():
        if not count:
            continue
        configured = {t["task"]: t["configured"] for t in tools.get(language, [])}
        for key, title, alarming in HEALTH_SIGNALS:
            value = "не измерялось"
            if key == "tests":
                value = (
                    f"файлов тестов в проекте: {test_files}"
                    if language in {"python", "csharp", "typescript", "powershell"}
                    else value
                )
            elif key == "lint":
                value = "настроен" if configured.get("Линтер и форматирование") else "не настроен"
                done = [
                    r
                    for k, r in ran.items()
                    if k in {"ruff", "tsc", "pyright", "psscriptanalyzer", "dotnet-format"}
                ]
                if done:
                    value += f"; запусков: {len(done)}, строк вывода: {sum(int(r['lines']) for r in done)}"
            elif key == "architecture":
                value = (
                    "правила настроены" if configured.get("Правила архитектуры") else "правил нет"
                )
            elif key == "dead":
                value = f"кандидатов в мёртвый код: {dead['total']}"
            elif key == "hotspots":
                value = f"горячих точек без теста: {sum(1 for h in hot if not h['has_test'])} из {len(hot)}"
            elif key == "build" and ran:
                bad = [
                    k
                    for k, r in ran.items()
                    if k.startswith("dotnet-build") and r["returncode"] != 0
                ]
                value = "сборка C# не проходит" if bad else "проверено запуском: сборка проходит"
            elif key == "dup" and "jscpd" in ran:
                value = "запускался (см. baseline/jscpd.json)"
            elif key == "seams":
                value = "оценивается в PR 2 (карта и швы)"
            rows.append(
                {"language": language, "signal": title, "value": value, "alarming_if": alarming}
            )
    return rows


def risks(
    facts: dict[str, Any],
    hot: list[dict[str, Any]],
    dead: dict[str, Any],
    tools: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """5–10 главных рисков по убыванию веса: что может сломаться и чем это грозит."""
    found: list[tuple[int, str, str]] = []
    git = facts["git"]
    if not git["is_repo"]:
        found.append(
            (
                100,
                "Проект не под git",
                "нельзя проверить, что анализ ничего не менял, и откатить ошибочную правку",
            )
        )
    if not facts["workflows"]:
        found.append(
            (
                90,
                "Нет автоматических проверок (CI)",
                "тесты и проверки запускает только человек: сломанное состояние может долго оставаться незамеченным",
            )
        )
    if not facts["claude_settings"]["hooks"]:
        found.append(
            (
                95,
                "Правила держатся на дисциплине агента, а не на принуждении",
                "нет hooks защиты: агент может изменить тесты, защищённые файлы и данные, если правило записано только словами",
            )
        )
    long = [i for i in facts["instruction_files"] if i["lines"] > 150]
    if long:
        names = ", ".join(f"{i['path']} ({i['lines']} строк)" for i in long)
        found.append(
            (
                70,
                "Инструкция агенту слишком длинная",
                f"{names} при пределе 150: чем длиннее текст, тем хуже модель его выполняет, и важные правила теряются",
            )
        )
    untested = [h for h in hot if not h["has_test"]]
    if untested:
        names = ", ".join(h["path"] for h in untested[:3])
        found.append(
            (
                85,
                f"Горячие точки без тестов: {len(untested)}",
                f"большие часто меняемые файлы ({names}…) правятся без защиты тестами: ошибки сосредоточены здесь",
            )
        )
    if dead["total"]:
        found.append(
            (
                60,
                f"Возможный мёртвый код: {dead['total']} кандидатов",
                "нельзя ни удалять, ни доверять картине проекта, пока владелец не ответил в QUESTIONS.md",
            )
        )
    for language, rows in tools.items():
        missing = [t["task"] for t in rows if not t["configured"] and "нет" not in t["tool"]]
        if len(missing) >= 3:
            found.append(
                (
                    55,
                    f"Мало инструментов проверки для {language}",
                    f"не настроено: {', '.join(missing[:4])}: дефекты находит только человек",
                )
            )
    big = [
        e
        for e in facts.get("equivalents", [])
        if e["role"] == "decisions" and e["path"].endswith(".md")
    ]
    if big:
        found.append(
            (
                50,
                "Журнал решений в одном файле",
                f"{big[0]['path']} трудно читать целиком и находить, что отменено чем; нужен индекс и разбиение на записи",
            )
        )
    renames = [e for e in facts.get("equivalents", []) if not e["is_standard"]]
    if renames:
        found.append(
            (
                40,
                f"Имена документов не по стандарту: {len(renames)}",
                "инструменты стандарта не найдут решения, цель и инциденты, пока имена не приведены (план предлагает переименования)",
            )
        )
    if facts["markdown_outside_docs_total"] > 10:
        found.append(
            (
                35,
                "Много заметок и планов вне docs/",
                f"{facts['markdown_outside_docs_total']} файлов: неясно, какой из них действует",
            )
        )
    if git["is_repo"] and not git["clean"]:
        found.append(
            (
                30,
                "В проекте есть несохранённые изменения",
                "анализ сравнивает состояние «до» и «после» только по тому, что есть сейчас",
            )
        )
    found.append(
        (
            20,
            "Состояние сборки и тестов не измерялось",
            "пока владелец не разрешил запуск проверок, карточка здоровья опирается только на файлы",
        )
    )
    found.sort(key=lambda r: -r[0])
    return [{"title": t, "consequence": c} for _, t, c in found[:MAX_RISKS]]


def goal_draft(project: Path, equivalents: list[dict[str, Any]]) -> str | None:
    """Черновик GOAL.md из файла цели проекта (в проект не попадает, лежит в parch-analysis/drafts/)."""
    goal = next((e for e in equivalents if e["role"] == "goal"), None)
    if goal is None or str(goal["path"]).endswith("/"):
        return None
    text = (project / str(goal["path"])).read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()][:25]
    plan = next((e for e in equivalents if e["role"] == "plan"), None)
    ready = 0
    if plan and not str(plan["path"]).endswith("/"):
        ready = len(
            re.findall(
                r"Готово, когда",
                (project / str(plan["path"])).read_text(encoding="utf-8", errors="replace"),
            )
        )
    return chr(10).join(
        [
            f"# GOAL — {project.name} ({DRAFT_MARK})",
            "",
            f"> Черновик построен из `{goal['path']}`; продукт и критерии утверждает владелец.",
            "",
            "## Что это (начало исходного документа)",
            "",
            *lines,
            "",
            "## Критерии готовности продукта",
            "",
            "Автоматически не выведены: критерии G1, G2… формулируются с владельцем."
            + (
                f" Источник условий готовности по шагам: `{plan['path']}`, строк «Готово, когда»: {ready}."
                if plan
                else ""
            ),
            "",
        ]
    ) + chr(10)


def adr_drafts(project: Path, equivalents: list[dict[str, Any]]) -> str | None:
    """Список записей журнала решений как черновики ADR со статусом proposed."""
    decisions = next((e for e in equivalents if e["role"] == "decisions"), None)
    if decisions is None or str(decisions["path"]).endswith("/"):
        return None
    text = (project / str(decisions["path"])).read_text(encoding="utf-8", errors="replace")
    entries = [
        ln.strip("- ").strip() for ln in text.splitlines() if re.match(r"^- \d{4}-\d{2}-\d{2}", ln)
    ]
    lines = [
        f"# Черновики записей решений ({DRAFT_MARK})",
        "",
        f"Источник: `{decisions['path']}`; записей с датой: {len(entries)}. Каждая запись станет отдельным ADR со статусом proposed, текст сохраняется как есть.",
        "",
    ]
    for number, entry in enumerate(entries[:40], start=1):
        lines.append(f"{number}. proposed: {entry[:160]}")
    if len(entries) > 40:
        lines.append(f"… и ещё {len(entries) - 40} записей")
    return chr(10).join(lines) + chr(10)
