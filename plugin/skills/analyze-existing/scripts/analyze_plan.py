# ruff: noqa: E501
"""План приведения к стандарту: таблица действий (docs/specs/F15-analyze-existing.md).

Строка плана это одно проверяемое действие: перенести, переименовать под имена стандарта, удалить (с доказательством),
перестроить. Предохранители записаны в самих строках и проверяются `validate`: ссылки обновляются вместе с переносом,
удаление только с архивной меткой и поимённым утверждением владельца, неприкосновенные зоны только с отдельным «да»,
код без тестов только после характеризационного теста или с пометкой «рискованно». Выполнять план будет блок F19.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MOVE, RENAME, DELETE, RESTRUCTURE = "Перенести", "Переименовать", "Удалить", "Перестроить"
CONTRACT = "Контрактный тест"  # добавляется только тест; безопасно, идёт первым
ACTIONS = (MOVE, RENAME, DELETE, RESTRUCTURE, CONTRACT)
CODE_SUFFIXES = {".py", ".cs", ".ts", ".tsx", ".ps1", ".psm1"}
LINKS = "обновить все ссылки (импорты, пути в скриптах, документы) и прогнать тесты"
RISKY = "рискованно, нужно отдельное «да»"
SEAM_TEST = "сначала контрактный тест шва"
ZONE_YES = "неприкосновенная зона: нужно отдельное «да»"
OUTSIDE_YES = "неприкосновенная зона, нужно отдельное «да»"  # файл пишет за пределы проекта
APPROVAL = "поимённое утверждение владельца"
GROUP_A, GROUP_B = "а", "б"  # швы: а) трогают игру, сейвы или data/; б) остальные
CONTRACT_VERIFY = "новый тест, который запускает или читает обе стороны шва и сверяет формат (колонки, поля, код возврата), падает при изменении шва"
CONTRACT_READ_ONLY = (
    "тест только читает файлы шва и не запускает скрипты (запуск может тронуть игру или сейв)"
)


def in_zone(path: str, zones: list[str]) -> bool:
    cleaned = path.replace(chr(92), "/").strip("/")
    return any(cleaned == z.strip("/") or cleaned.startswith(z.strip("/") + "/") for z in zones)


def touches(old: str, seam_file: str) -> bool:
    """План меняет файл шва или папку, в которой он лежит."""
    base = old.rstrip("/")
    return bool(base) and (seam_file == base or seam_file.startswith(base + "/"))


def row(action: str, what: str, old: str, new: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": action,
        "what": what,
        "old": old,
        "new": new,
        "method": "git mv" if action in {MOVE, RENAME} else "",
        "verify": LINKS if action in {MOVE, RENAME} else "",
        "proof": "",
        "archive_tag": "",
        "confirmation": "",
        "precondition": "",
        "risk": "безопасно",
        "code_without_tests": False,
        "needs_owner_yes": False,
        "seams_red": [],
    }
    base.update(extra)
    return base


def has_test_for(path: str, rel: list[str]) -> bool:
    stem = Path(path).stem.lower()
    names = {
        f"test_{stem}",
        f"{stem}_test",
        f"{stem}tests",
        f"{stem}.test",
        f"{stem}.tests",
        f"{stem}.spec",
    }
    return any(Path(p).stem.lower() in names for p in rel if p != path)


def code_files_under(path: str, rel: list[str]) -> list[str]:
    prefix = path.rstrip("/") + "/"
    return [
        p
        for p in rel
        if (p == path or p.startswith(prefix)) and Path(p).suffix.lower() in CODE_SUFFIXES
    ]


def build_plan(
    equivalents: list[dict[str, Any]],
    rel: list[str],
    protected: list[str],
    dead_confirmed: list[dict[str, Any]] | None = None,
    tag_date: str = "",
    seams: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Строки плана от безопасных к рискованным. Удаления только по ответам владельца (`dead_confirmed`)."""
    rows: list[dict[str, Any]] = []
    for eq in equivalents:
        if eq["is_standard"]:
            continue
        action = RENAME if eq["action"] == RENAME else RESTRUCTURE
        old, new = str(eq["path"]), str(eq["standard"])
        text = f"{eq['title']}: {eq['note']}"
        rows.append(row(action, text, old, new))
    for item in dead_confirmed or []:
        name, path = str(item["name"]), str(item["path"])
        rows.append(
            row(
                DELETE,
                f"{item.get('kind', 'код')} {name}",
                path,
                "",
                proof=f"поиск «{name}» по всем текстам проекта: 1 вхождение (объявление); владелец ответил «удалить»",
                archive_tag=f"archive/{name}-{tag_date or 'ДАТА'}",
                confirmation=APPROVAL,
            )
        )
    for item in rows:
        touched = [str(item["old"]), str(item["new"])]
        if any(p and in_zone(p, protected) for p in touched):
            item["risk"], item["needs_owner_yes"] = ZONE_YES, True
        if item["action"] in {MOVE, RENAME} and item["old"]:
            code = code_files_under(str(item["old"]), rel)
            if code and not all(has_test_for(p, rel) for p in code):
                if item["risk"] != ZONE_YES:  # метка зоны не затирается пометкой «рискованно»
                    item["risk"] = RISKY
                item["needs_owner_yes"] = True
                item["code_without_tests"] = True
                item["precondition"] = (
                    "сначала характеризационный тест, фиксирующий нынешнее поведение"
                )
        if item["action"] == DELETE:
            item["needs_owner_yes"] = True
        red_here = [
            str(seam["id"])
            for seam in seams or []
            if seam["red"] and any(touches(str(item["old"]), f) for f in seam["files"])
        ]
        if red_here:  # изменение шва только после контрактного теста этого шва
            item["seams_red"] = red_here
            if item["risk"] != ZONE_YES:
                item["risk"] = RISKY
            item["needs_owner_yes"] = True
            note = f"{SEAM_TEST} {', '.join(red_here)}"
            item["precondition"] = (
                f"{item['precondition']}; {note}" if item["precondition"] else note
            )
    for seam in seams or []:
        if (
            seam["red"] and seam.get("group", GROUP_A) == GROUP_A
        ):  # группа «б» остаётся условием выше (red_here)
            where = ", ".join(seam["evidence"][:2])
            outside = list(seam.get("outside_write") or [])
            zoned = [f for f in seam["files"] if in_zone(f, protected)]
            note = f"; шов пишет за пределы проекта ({', '.join(outside)})" if outside else ""
            note += f"; файл шва в неприкосновенной зоне ({', '.join(zoned)})" if zoned else ""
            rows.append(
                row(
                    CONTRACT,
                    f"шов {seam['id']} ({seam['kind']}: {seam['from']} → {seam['to']}, {where}) без контрактного теста{note}",
                    "",
                    "",
                    verify=f"{CONTRACT_VERIFY}; {CONTRACT_READ_ONLY}",
                    risk=f"{OUTSIDE_YES} на любое изменение файла шва; сам тест безопасен"
                    if outside or zoned
                    else "трогает игру, сейвы или data/: тест до любых изменений; сам тест безопасен",
                )
            )
    order = {CONTRACT: -1, RENAME: 0, MOVE: 1, RESTRUCTURE: 2, DELETE: 3}
    rows.sort(key=lambda r: (r["needs_owner_yes"], order[r["action"]], str(r["old"])))
    return rows


def validate(rows: list[dict[str, Any]], protected: list[str]) -> list[str]:
    """Нарушения предохранителей: каждая строка плана обязана их соблюдать."""
    problems: list[str] = []
    for item in rows:
        where = f"{item.get('action')} {item.get('old') or item.get('what')}"
        if item.get("action") not in ACTIONS:
            problems.append(f"{where}: неизвестное действие")
            continue
        if item["action"] in {MOVE, RENAME}:
            if item.get("method") != "git mv":
                problems.append(f"{where}: перенос и переименование идут через git mv")
            if "ссылки" not in str(item.get("verify")) or "тесты" not in str(item.get("verify")):
                problems.append(f"{where}: перенос без обновления всех ссылок и зелёных тестов")
        if item["action"] == DELETE:
            if not str(item.get("proof")).strip():
                problems.append(
                    f"{where}: удаление без доказательства (мёртвый код, дубль, нет ссылок)"
                )
            if not str(item.get("archive_tag")).strip():
                problems.append(f"{where}: удаление без архивной метки в git")
            if "поимённое" not in str(item.get("confirmation")):
                problems.append(f"{where}: удаление без поимённого утверждения владельца")
        touched = [str(item.get("old") or ""), str(item.get("new") or "")]
        if any(p and in_zone(p, protected) for p in touched) and not item.get("needs_owner_yes"):
            problems.append(f"{where}: затронута неприкосновенная зона, нужно отдельное «да»")
        if item.get("seams_red") and SEAM_TEST not in str(item.get("precondition")):
            problems.append(f"{where}: изменение шва без контрактного теста этого шва")
        if (
            item["action"] in {MOVE, RENAME}
            and item.get("old")
            and str(item.get("risk")) != RISKY
            and not str(item.get("precondition")).strip()
            and item.get("code_without_tests")
        ):
            problems.append(
                f"{where}: код без тестов без характеризационного теста и без пометки «рискованно»"
            )
    return problems


def plan_markdown(
    rows: list[dict[str, Any]],
    protected: list[str],
    outside: dict[str, list[str]] | None = None,
    seams: list[dict[str, Any]] | None = None,
) -> str:
    """PLAN.md для parch-analysis/: таблица действий и предохранители."""
    lines = [
        "# План приведения к стандарту (черновик анализа)",
        "",
        "Выполняется отдельным блоком F19 маленькими PR от безопасного к рискованному. План утверждается целиком, "
        "необратимые пункты (удаления и всё, где нужно отдельное «да») по одному.",
        "",
        "| № | Действие | Что | Откуда → куда | Предохранитель | Риск |",
        "|---|---|---|---|---|---|",
    ]
    for number, item in enumerate(rows, start=1):
        route = f"`{item['old']}` → `{item['new']}`" if item["new"] else f"`{item['old']}`"
        if not item["old"]:
            route = "—"
        guard = item["verify"] or (
            f"{item['proof']}; метка {item['archive_tag']}; {item['confirmation']}"
            if item["action"] == DELETE
            else "архитектурные правила по уровням проходят в CI"
        )
        if item["precondition"]:
            guard += f"; {item['precondition']}"
        lines.append(
            f"| {number} | {item['action']} | {item['what']} | {route} | {guard} | {item['risk']} |"
        )
    if not rows:
        lines.append("| | | Действий не требуется | | | |")
    red = [s for s in seams or [] if s["red"]]
    if red:
        a = [s for s in red if s.get("group", GROUP_A) == GROUP_A]
        b = [s for s in red if s.get("group", GROUP_A) == GROUP_B]
        lines += [
            "",
            f"Швы без контрактного теста: {len(red)}. **Группа (а)**, трогают игру, сейвы или `data/`: {len(a)}, "
            "каждому строка «Контрактный тест» выше, тест идёт до любых изменений. "
            f"**Группа (б)**, остальные: {len(b)}, отдельных строк нет: условие «сначала контрактный тест» действует, "
            "только если строка плана меняет одну из сторон шва.",
            "",
            "Группа (а), почему: "
            + "; ".join(f"{s['id']} ({', '.join(s.get('group_reasons', [])[:2])})" for s in a)
            + ".",
            "",
            "Группа (б): " + ", ".join(str(s["id"]) for s in b) + ".",
        ]
    if outside:
        lines += [
            "",
            f"Файлы, которые по тексту пишут за пределы проекта (папка игры, BepInEx, Steam): {OUTSIDE_YES}. "
            + ", ".join(f"`{path}`" for path in sorted(outside))
            + ".",
        ]
    lines += [
        "",
        "Предохранители: перенос только вместе с обновлением всех ссылок и зелёными тестами; переносы через `git mv`; "
        "удаление только с архивной меткой в git и поимённым утверждением владельца; "
        + (
            f"неприкосновенные зоны ({', '.join(protected)}) не трогать без отдельного «да»; "
            if protected
            else ""
        )
        + "код без тестов только после характеризационного теста или с пометкой «рискованно».",
        "",
        "Удаления в таблицу попадают только после ваших ответов в QUESTIONS.md.",
    ]
    return chr(10).join(lines) + chr(10)


def clean_zone_list(raw: object) -> list[str]:
    """Список неприкосновенных зон из запроса: только непустые строки, слэши прямые."""
    zones: list[str] = []
    if isinstance(raw, list):
        for item in raw:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(item, str) and re.search(r"\w", item):
                zones.append(item.replace(chr(92), "/").strip("/"))
    return zones
