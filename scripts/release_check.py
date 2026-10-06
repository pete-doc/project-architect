# ruff: noqa: E501
"""Сверка версии плагина с последним тегом выпуска (docs/LESSONS.md: автоматика вместо напоминания).

    python scripts/release_check.py            сообщает, сколько файлов `plugin/` изменилось после тега
    python scripts/release_check.py --strict   то же, но «изменено, а версия не поднята» и «тегов нет» это ошибки (выпуск)

Почему так: установленный плагин обновляется только при смене `version` в `plugin/.claude-plugin/plugin.json`.
Между выпусками `plugin/` менять можно (поле версии трогает только PR «выпуск», AGENTS.md, правило 11), поэтому
в обычном режиме расхождение лишь показывается; ошибкой оно становится в `--strict` (PR «выпуск») и всегда
для нарушений, которых не должно быть никогда: версия ниже версии тега, имя тега не равно `v` + версия в нём.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "plugin/.claude-plugin/plugin.json"
TAG_PATTERN = "v[0-9]*"


@dataclass
class Report:
    tag: str | None = None
    tag_version: str | None = None
    version: str | None = None
    changed: list[str] = field(default_factory=lambda: [])
    errors: list[str] = field(default_factory=lambda: [])

    @property
    def drift(self) -> bool:
        """Файлы плагина изменились после тега, а версия осталась прежней."""
        return bool(self.changed) and self.version == self.tag_version


def git(root: Path, *args: str) -> str | None:
    done = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    return done.stdout if done.returncode == 0 else None


def version_of(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        value = json.loads(text).get("version")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def parse(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def evaluate(root: Path) -> Report:
    report = Report()
    manifest = root / MANIFEST
    report.version = (
        version_of(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
    )
    if report.version is None:
        report.errors.append(f"в {MANIFEST} нет строки version: плагин не установится с версией")
        return report
    tag = git(root, "describe", "--tags", "--abbrev=0", "--match", TAG_PATTERN, "HEAD")
    if tag is None or not tag.strip():
        return report  # тегов выпуска нет: сверять не с чем (в --strict это ошибка, см. render)
    report.tag = tag.strip()
    report.tag_version = version_of(git(root, "show", f"{report.tag}:{MANIFEST}"))
    if report.tag_version is None:
        report.errors.append(f"в теге {report.tag} нет читаемой версии плагина в {MANIFEST}")
        return report
    if report.tag != f"v{report.tag_version}":
        report.errors.append(
            f"тег {report.tag} не равен v{report.tag_version}: версия в манифесте на этом теге другая"
        )
    now, then = parse(report.version), parse(report.tag_version)
    for name, parsed in ((report.version, now), (report.tag_version, then)):
        if parsed is None:
            report.errors.append(
                f"версию {name!r} нельзя разобрать: нужен вид N.N.N, без суффиксов"
            )
    if now is not None and then is not None and now < then:
        report.errors.append(f"версия {report.version} ниже версии тега {report.tag}")
    listed = git(root, "diff", "--name-only", report.tag, "--", "plugin")
    report.changed = [
        line.strip()
        for line in (listed or "").splitlines()
        if line.strip() and line.strip() != MANIFEST
    ]
    return report


def render(report: Report, strict: bool) -> tuple[list[str], int]:
    """Строки для вывода и код выхода."""
    lines = [f"ОШИБКА: {text}" for text in report.errors]
    code = 1 if report.errors else 0
    if report.errors or report.tag is None:
        if report.tag is None and not report.errors:
            if strict:
                lines.append(
                    "ОШИБКА: тегов выпуска (v*) нет, версию плагина сверять не с чем: "
                    "выполните `git fetch --tags` или поставьте тег предыдущего выпуска."
                )
                code = 1
            else:
                lines.append("Тегов выпуска (v*) нет: сверять версию плагина не с чем.")
        return lines, code
    if report.drift:
        count = len(report.changed)
        lines.append(
            f"Плагин: версия {report.version} = тег {report.tag}, но после тега изменено файлов в plugin/: {count}."
            " Установленный у владельца плагин этих правок не получит, пока не выйдет выпуск (версия плюс тег)."
        )
        if strict:
            lines.append("ОШИБКА: выпуск без повышения версии: поднимите version в plugin.json.")
            code = 1
    elif report.changed:
        lines.append(
            f"Плагин: версия {report.version} выше тега {report.tag}: выпуск подготовлен, "
            f"после слияния поставьте тег v{report.version}."
        )
    else:
        lines.append(
            f"Плагин: версия {report.version}, после тега {report.tag} файлы plugin/ не менялись."
            if report.version == report.tag_version
            else f"Плагин: версия {report.version} поднята без изменений plugin/ (тег {report.tag})."
        )
    return lines, code


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    lines, code = render(evaluate(ROOT), strict="--strict" in args)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
