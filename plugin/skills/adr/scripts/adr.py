"""Работа с ADR для `/parch:adr`: создать запись из шаблона и пересобрать индекс docs/adr/README.md.

Вход: JSON-объект на stdin. Выход: JSON-объект на stdout (по-русски, для агента).

    {"command": "new", "title": "Выбор базы данных", "type": "irreversible", "project_dir": "."}
    {"command": "index", "project_dir": "."}

Статус новой записи всегда `proposed`: статус `accepted` ставит только владелец.
"""

from __future__ import annotations

import io
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = PLUGIN_ROOT / "templates" / "docs" / "adr" / "0000-template.md"
TYPES = {"reversible": "обратимое", "irreversible": "необратимое"}
INDEX_NAME = "README.md"

_TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        [
            "a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p",
            "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya",
        ],
        strict=True,
    )
)  # fmt: skip
_ADR_FILE = re.compile(r"^(\d{4})-.+\.md$")
_TITLE = re.compile(r"^#\s*ADR-(\d{4})\.\s*(.+?)\s*$")
_FIELD = re.compile(r"^\s*[-*]\s*(Статус|Тип решения|Дата)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_IRREVERSIBLE_BLOCK = re.compile(
    r"<!-- irreversible:start -->\n.*?<!-- irreversible:end -->\n\n?", re.S
)
_MARKERS = re.compile(r"<!-- irreversible:(start|end) -->\n")


def slugify(title: str) -> str:
    """Имя файла: латиница, цифры и дефисы (русский заголовок транслитерируется)."""
    letters = "".join(_TRANSLIT.get(ch, ch) for ch in title.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", letters).strip("-")
    return slug[:60].strip("-") or "adr"


def adr_dir(project: Path) -> Path:
    return project / "docs" / "adr"


def numbered_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir() if (m := _ADR_FILE.match(p.name)) and m.group(1) != "0000"
    )


def next_number(folder: Path) -> int:
    numbers = [int(p.name[:4]) for p in numbered_files(folder)]
    return max(numbers, default=0) + 1


def render(number: int, title: str, kind: str, today: str, template: str) -> str:
    text = template
    if kind == "reversible":
        text = _IRREVERSIBLE_BLOCK.sub("", text)
    else:
        text = _MARKERS.sub("", text)
    replacements = {
        "{{NUMBER}}": f"{number:04d}",
        "{{TITLE}}": title,
        "{{TYPE}}": TYPES[kind],
        "{{DATE}}": today,
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def read_summary(path: Path) -> dict[str, str]:
    """Название, статус, тип и дата записи; чего не нашли, то прочерк."""
    summary = {
        "number": path.name[:4],
        "title": path.stem[5:],
        "status": "—",
        "type": "—",
        "date": "—",
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]:
        if match := _TITLE.match(line):
            summary["title"] = match.group(2)
        elif match := _FIELD.match(line):
            label = match.group(1).lower()
            value = re.split(r"\s*[(;,]", match.group(2), maxsplit=1)[0].strip()
            key = {"статус": "status", "тип решения": "type", "дата": "date"}[label]
            summary[key] = value or "—"
    return summary


def build_index(project: Path) -> Path:
    folder = adr_dir(project)
    rows: list[str] = []
    for path in numbered_files(folder):
        s = read_summary(path)
        title = s["title"].replace("|", "/")
        link = f"[{s['number']}]({path.name})"
        rows.append(f"| {link} | {title} | {s['status']} | {s['type']} | {s['date']} |")
    header = (
        "# Решения (ADR)\n\n"
        "> Индекс собирается автоматически командой `/parch:adr`, вручную его не правят.\n"
        "> Статус `proposed` значит «ждёт утверждения владельца»,\n"
        "> статус `accepted` ставит только владелец.\n\n"
        "| № | Решение | Статус | Тип | Дата |\n"
        "|---|---|---|---|---|\n"
    )
    text = header + "\n".join(rows) + ("\n" if rows else "")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / INDEX_NAME
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def create(project: Path, title: str, kind: str) -> dict[str, Any]:
    if kind not in TYPES:
        raise ValueError("type должен быть reversible или irreversible")
    if not title.strip():
        raise ValueError("нужен заголовок решения")
    folder = adr_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    project_template = folder / "0000-template.md"
    template = (project_template if project_template.is_file() else TEMPLATE).read_text(
        encoding="utf-8"
    )
    number = next_number(folder)
    today = datetime.now(UTC).date().isoformat()
    path = folder / f"{number:04d}-{slugify(title)}.md"
    path.write_text(
        render(number, title.strip(), kind, today, template), encoding="utf-8", newline="\n"
    )
    index = build_index(project)
    return {
        "created": path.relative_to(project).as_posix(),
        "index": index.relative_to(project).as_posix(),
        "number": f"{number:04d}",
        "type": TYPES[kind],
        "status": "proposed",
        "next": (
            "Заполни разделы записи, не меняя статус proposed. "
            + (
                "Решение необратимое: напиши раздел «Объяснение для владельца» на одну страницу, "
                "простыми словами."
                if kind == "irreversible"
                else "Решение обратимое: объяснение для владельца не нужно."
            )
        ),
    }


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    request: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
    project = Path(str(request.get("project_dir") or ".")).resolve()
    command = request.get("command")
    if command == "new":
        result = create(project, str(request.get("title", "")), str(request.get("type", "")))
    elif command == "index":
        result = {"index": build_index(project).relative_to(project).as_posix()}
    else:
        raise ValueError("command должен быть new или index")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as error:
        sys.stderr.write(f"adr: {error}\n")
        sys.exit(1)
