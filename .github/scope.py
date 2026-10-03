"""Что изменил PR: нужны ли тесты, hooks на Windows и какие языки менялись (ADR-0013).

Читает список файлов PR со стандартного ввода (по одному пути в строке), печатает строки
`имя=значение` для $GITHUB_OUTPUT. Медленные тесты урезанного прогона выбираются по языкам.
"""

from __future__ import annotations

import fnmatch
import sys

LANGUAGES = ("python", "typescript", "csharp", "powershell")
# Эти пути касаются всех языков сразу: общий код проверок, настройки тестов, сам CI.
SHARED = (
    "plugin/templates/ci/parch/*",
    "tests/conftest.py",
    "pyproject.toml",
    "requirements-dev.txt",
    ".github/*",
)
OWN = {
    "python": (
        "tests/test_ci_python.py",
        "tests/projects/python_shop/*",
        "plugin/templates/ci/python.yml",
    ),
    "typescript": (
        "tests/test_ci_typescript.py",
        "tests/projects/ts_shop/*",
        "plugin/templates/ci/typescript.yml",
    ),
    "csharp": (
        "tests/test_ci_csharp.py",
        "tests/projects/cs_shop/*",
        "plugin/templates/ci/csharp.yml",
    ),
    "powershell": ("tests/test_ci_powershell.py", "plugin/templates/ci/powershell.yml"),
}
# Правки только текста идут без тестов: в CI проверяется только standard. Тесты не проверяют
# смысл текста; для защищённых текстовых файлов проверка это «сливай» владельца (ADR-0003).
# Текстом считаются AGENTS.md, CLAUDE.md, docs/**/*.md (ADR, BACKLOG и т.п.) и отчёты в state/.
# Исключение: файлы, которые читают проверки и тесты (RULE_FILES), и всё остальное в state/
# (baseline.json, features.json и будущие файлы правил): они считаются кодом, нужен полный прогон.
NO_CODE = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/*.md",
    "state/incidents/*",
    "state/acceptance/*",
    "state/STATUS.md",
)
RULE_FILES = (
    "docs/GOAL.md",
    "docs/MODULES.md",
    "docs/CONSTITUTION.md",
    "docs/STANDARD.md",
    "CONSTITUTION.md",
    "GOAL.md",
)
HOOKS = ("plugin/hooks/*", "plugin/templates/*", ".github/*")


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def scope(files: list[str] | None) -> dict[str, str]:
    """files=None: список файлов получить не удалось, идут все проверки."""
    code = hooks = files is None
    languages = set(LANGUAGES) if files is None else set[str]()
    for path in files or []:
        code = code or not matches(path, NO_CODE) or matches(path, RULE_FILES)
        hooks = hooks or matches(path, HOOKS)
        if matches(path, SHARED):
            languages |= set(LANGUAGES)
        languages |= {lang for lang, patterns in OWN.items() if matches(path, patterns)}
    selector = " or ".join(
        ["not slow", *(f"lang_{lang}" for lang in LANGUAGES if lang in languages)]
    )
    return {
        "code": str(code).lower(),
        "hooks": str(hooks).lower(),
        # jscpd (проверка дублей) ставится через node: он нужен Python и TypeScript
        "node": str(bool(languages & {"python", "typescript"})).lower(),
        "csharp": str("csharp" in languages).lower(),
        "powershell": str("powershell" in languages).lower(),
        "selector": selector,
    }


def main() -> int:
    if "--unknown" in sys.argv[1:]:  # список файлов получить не удалось: идут все проверки
        files = None
    else:
        files = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    for name, value in scope(files).items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
