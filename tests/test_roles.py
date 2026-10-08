# ruff: noqa: E501
"""Роли агентов (2026-10-07): роль tester, согласованность ролей в hooks, шаблонах, навыках и STANDARD.md.

Живой прогон F20 (шаг 4) показал: правило охраны путей разрешало править тесты только ролям architect и tester,
а этих агентов в плагине не было, поэтому написать тест не мог никто. Здесь два вида проверок:
1. роль tester настоящая (файл агента) и охрана путей относится к ней и к другим ролям так, как заявлено;
2. любая роль, на которую ссылаются hooks, шаблоны, навыки и STANDARD.md, либо есть в `plugin/agents/`, либо названа
   в одной строке BACKLOG «РОЛИ БЕЗ АГЕНТА». Плохие примеры на подставном тексте: правило с несуществующей ролью красное.
"""

import re
from pathlib import Path

import pytest
from conftest import bash, file_call, run_hook

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "plugin" / "agents"
BACKLOG = ROOT / "docs" / "BACKLOG.md"
MARKER = "РОЛИ БЕЗ АГЕНТА"
CANDIDATES = "architect|tester|implementer|reviewer|janitor|explorer|planner|researcher|designer|debugger|analyst|inspector"


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: первая строка файла роли должна быть ---"
    head = text.split("\n---\n", 1)[0].removeprefix("---\n")
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in head.splitlines())}


def agent_names() -> set[str]:
    return {frontmatter(path)["name"] for path in sorted(AGENTS.glob("*.md"))}


# ---------- роль tester ----------


def test_the_tester_role_file_has_the_fields_claude_code_requires() -> None:
    """Плохой пример: без name и description Claude Code молча пропускает агента (как было с отсутствующей ролью)."""
    fields = frontmatter(AGENTS / "tester.md")
    assert fields["name"] == "tester" and ":" not in fields["name"]
    assert fields["description"], "без description агента Claude Code пропускает молча"
    assert set(fields) <= {"name", "description", "tools", "model"}
    for ignored in ("hooks", "mcpServers", "permissionMode"):
        assert ignored not in fields  # плагин-агенты эти поля игнорируют
    assert {"Read", "Edit", "Write", "Bash"} <= {t.strip() for t in fields["tools"].split(",")}
    lines = (AGENTS / "tester.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 40, f"tester.md: {len(lines)} строк, предел 40 (STANDARD.md, раздел 12)"


def test_the_tester_instruction_says_what_the_standard_requires() -> None:
    """Пишет тесты по описанию блока, не подгоняет под код, код не правит, при необходимости правки кода останавливается."""
    text = (AGENTS / "tester.md").read_text(encoding="utf-8")
    for must in (
        "state/features.json",  # откуда блок и файл тестов
        "docs/specs/",
        "не угадывай",  # не определено в описании: вопрос, а не догадка
        "docs/QUESTIONS.md",
        "заведомо плохой пример",
        "вызывает настоящую функцию проекта",  # плохой пример проходит через код проекта
        "monkeypatch",
        "объявленную внутри самого теста",  # локальная копия плохой реализации не засчитывается
        "Саму проверяемую функцию не подменяют",  # monkeypatch подменяет зависимость, а не функцию
        "Не правишь код блока",  # код не правит
        "остановись и сообщи",  # нужна правка кода: сообщает
        "state/baseline.json",  # baseline обновляет владелец
        "не ослабляют",  # тест не подгоняют и не ослабляют
        "STANDARD.md, раздел 5, п. 3",  # тест до кода, без кода падает
        "падает на неверном ожидании или заведомо неверной реализации",  # показывает, что тест умеет падать
    ):
        assert must in text, must
    assert "skip" in text and "xfail" in text  # пропуски запрещены


def test_guard_paths_lets_tester_write_tests_and_nobody_else(project: Path) -> None:
    """Для каждого агента плагина: правит тесты только tester (и architect, которого нет); остальные и основная сессия блокированы."""
    target = project / "tests" / "test_convert.py"
    for name in sorted(agent_names()):
        result = run_hook("guard_paths.py", file_call("Write", target), project, f"parch:{name}")
        if name == "tester":
            assert result.code == 0 and not result.blocked, (
                result.stderr
            )  # плохой пример для остальных ниже
        else:
            assert result.blocked and "architect и tester" in (result.stdout + result.stderr), name
    nobody = run_hook("guard_paths.py", file_call("Write", target), project, None)
    assert nobody.blocked and "основная сессия без роли" in (nobody.stdout + nobody.stderr)


@pytest.mark.parametrize(
    "rel",
    [
        "src/convert.py",  # код блока
        "state/baseline.json",  # храповик: утверждает владелец
        "state/features.json",
        ".circleci/config.yml",  # CI
        "pyproject.toml",  # настройки проверок
        "docs/GOAL.md",
        "docs/specs/F1.md",  # спецификацию пишет не tester
        "plugin/templates/docs/specs/x.md",  # спецификация не в корневом docs/
        "src/contests/x.py",  # папка «оканчивается на tests», но это не тестовая папка
        "src/latest.cs",  # имя оканчивается на test, но это не тест
        "src/Shop.Testing/Shop.cs",  # не проект тестов
        "src/Shop.Tests.Helpers/Shop.cs",  # не проект тестов
        "specs/x.md",  # папка specs для tester не тестовая
        "src/app.test/main.py",  # суффикс .test не делает папку тестовой
        "plugin/hooks.test/x.py",
        "src/hooks.tests/main.py",  # суффикс .tests: только файлы C#, не любой код
    ],
)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_tester_cannot_write_anything_but_tests_and_the_questions_file(
    rel: str, tool: str, project: Path
) -> None:
    """Плохие примеры: tester пишет код, baseline, CI, настройки, цель, спецификацию: блок с понятным сообщением по-русски."""
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, "parch:tester")
    assert result.blocked, rel
    shown = result.stdout + result.stderr
    assert "Роль tester правит только тесты и docs/QUESTIONS.md" in shown and rel.lower() in shown
    assert "implementer" in shown  # сообщение говорит, к кому идти за правкой кода


@pytest.mark.parametrize(
    "rel",
    [
        "tests/test_convert.py",
        "tests/conftest.py",
        "tests/data/sample.json",
        "tests/unit/test_deep.py",
        "src/test_helpers.py",  # маска test_*.py
        "src/shop_test.py",
        "web/app.spec.ts",
        "src/Shop.Tests.cs",
        "Shop.Tests/ShopTests.cs",  # проект тестов C#
        "docs/QUESTIONS.md",
        "docs/questions.md",
    ],
)
def test_tester_can_write_tests_and_the_questions_file(rel: str, project: Path) -> None:
    """Хорошие случаи: тест, фикстуры и данные тестов в `tests/`, вопросы по описанию блока в `docs/QUESTIONS.md`."""
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, "parch:tester")
    assert result.code == 0 and not result.blocked, (rel, result.stdout + result.stderr)


def test_tester_is_blocked_on_code_written_through_the_shell_too(project: Path) -> None:
    result = run_hook("guard_paths.py", bash("echo x > src/convert.py"), project, "parch:tester")
    assert result.blocked and "Роль tester правит только тесты" in (result.stdout + result.stderr)
    harmless = run_hook(
        "guard_paths.py", bash("python -m pytest tests/test_convert.py"), project, "parch:tester"
    )
    assert harmless.code == 0 and not harmless.blocked  # запуск тестов не запись


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        # обычная работа tester: должно проходить без ложных блоков (правило 9 остановило бы работу)
        ("python -m pytest tests/test_convert.py", True),
        ("python -m pytest tests/ > test-report.xml", True),  # то же разрешает guard_shell_writes
        ("python -m pytest --junitxml=test-report.xml > junit.xml", True),
        ("python -m pytest tests/ > /dev/null 2>&1", True),
        ("git status", True),
        ("git diff", True),
        ("git log --oneline", True),
        ("git add tests/test_convert.py", True),
        ("git commit -m 'тест'", True),
        ("git checkout main", True),  # ветка, не путь
        ("git checkout -b F1-tests", True),
        ("git checkout -- tests/test_convert.py", True),
        ("git restore tests/test_convert.py", True),
        ("git rm tests/test_old.py", True),
        ("git mv tests/a.py tests/test_b.py", True),
        ("python -m pytest > $TEMP/out.txt", True),  # временный файл вне проекта
        ("python -m pytest | tee $TEMP/log.txt", True),
        ("python -m pytest > /tmp/out.txt", True),
        ("python -m pytest --cov > .coverage", True),  # отчёт в корне проекта
        ("python -m pytest --cov-report=html > htmlcov/index.html", True),
        ("sed -i 's/a/b/' tests/test_convert.py", True),  # выражение это не путь
        ("perl -i -pe 's/a/b/' tests/test_convert.py", True),
        ("echo x >&2", True),  # поток stderr, не файл
        ("python -m pytest > /dev/null 2>&1 >&2", True),
        ("echo x > &2", True),
        ("sed -i -e 's/a/b/' -e 's/c/d/' tests/test_convert.py", True),  # несколько выражений
        ("sed -i --expression='s/a/b/' tests/test_convert.py", True),
        ("sed -i -f script.sed tests/test_convert.py", True),
        ("perl -i -pe 's/a/b/' -e 's/c/d/' tests/test_convert.py", True),
        ("cp src/a.py tests/", True),  # сама папка тестов как цель
        ("mv junit.xml tests/", True),
        ("git checkout -- tests/", True),
        ("git checkout HEAD -- tests/", True),
        ("git restore tests", True),
        ("git restore --source main tests/a.py", True),  # значение --source это не путь
        ("git restore -s main tests/a.py", True),
        ("cp -t tests src/a.py", True),  # пишется только в tests, источник не меняется
        ("cd tests && touch a.py", True),  # после cd пути считаются от tests
        ("cd tests && rm -rf __pycache__", True),
        ("rm -rf Shop.Tests/bin", True),
        ("rm -rf Shop.Tests/obj", True),
        ("rm -rf TestResults", True),
        ("rm -rf .pytest_cache", True),
        ("rm -rf tests/__pycache__", True),
        ("touch tests/test_new.py", True),
        ("cp tests/a.py tests/test_b.py", True),
        # плохие примеры: запись в код и настройки
        ("git checkout -- src/convert.py", False),
        ("git restore src/convert.py", False),
        ("git rm src/convert.py", False),
        ("git mv src/convert.py src/old.py", False),
        ("rm src/convert.py", False),
        ("touch src/new.py", False),
        ("cp tests/a.py src/convert.py", False),
        ("echo x > src/convert.py", False),
        ("python -m pytest tests/ > out.txt", False),  # посторонний файл в корне проекта
        ("echo x >> state/baseline.json", False),
        ("sed -i 's/a/b/' src/convert.py", False),
        ("perl -i -pe 's/a/b/' src/convert.py", False),
        ("cp -t src tests/a.py", False),  # настоящая цель стоит после -t
        ("cp --target-directory=src tests/a.py", False),
        ("mv -t src tests/a.py", False),
        ("cp tests/a.py src/htmlcov/convert.py", False),  # имя отчёта не оправдывает код
        ("touch src/junit.xml", False),
        ("touch src/.coverage", False),
        ("cp tests/a.py src/htmlcov/x.py", False),
        ("rm -rf src/__pycache__/x.py", False),  # файл внутри кэша не кэш
        ("rm -rf docs/specs/__pycache__", False),
        ("cp -vt src tests/a.py", False),  # цель в связке флагов
        ("cp -tsrc tests/a.py", False),  # цель слитно с флагом
        ("perl -pi.bak -e 's/a/b/' src/convert.py", False),
        ("perl -pi -e 's/a/b/' src/convert.py", False),
        ("sed --in-place=.bak 's/a/b/' src/convert.py", False),
        ("sed -Ei 's/a/b/' src/convert.py", False),
        ("sed -ni 's/a/b/p' src/convert.py", False),
        ("cd tests && rm ../src/convert.py", False),  # cd внутри команды учитывается
        ("cd tests && cp x ../src/y.py", False),
        ("cd src && touch a.py", False),
        ("touch $TEMP/../src/a.py", False),  # выход из временной папки в код
        ("cp tests/a.py $TEMP/../src/a.py", False),
        ("sed -i -e 's/a/b/' -e 's/c/d/' src/convert.py", False),
        ("sed -i -f script.sed src/convert.py", False),
        ("echo x >&2 > src/a.py", False),  # поток не прячет настоящую запись
        ("rm -rf Shop.Tests/Shop.cs.py", False),
        ("git checkout -- .", False),  # корень проекта: понятный блок, не внутренняя ошибка
        ("rm -rf .", False),
    ],
)
def test_tester_shell_commands_are_blocked_only_when_they_write_outside_the_tests(
    command: str, allowed: bool, project: Path
) -> None:
    """Обычная работа tester в оболочке проходит, запись в код и baseline блокируется: правило запрета
    нельзя строить на грубой оценке «что команда пишет» (лишнее слово даёт ложный блок)."""
    result = run_hook("guard_paths.py", bash(command), project, "parch:tester")
    shown = result.stdout + result.stderr
    if allowed:
        assert result.code == 0 and not result.blocked, (command, shown)
    else:
        assert result.blocked and "Роль tester правит только тесты" in shown, (command, shown)


def test_other_roles_keep_the_rough_git_path_estimate(project: Path) -> None:
    """Для остальных ролей git-пути по-прежнему берутся грубо (слово подкоманды как путь): защищённый
    путь после `git checkout` не проскакивает, хотя `--` нет."""
    result = run_hook(
        "guard_paths.py", bash("git checkout state/baseline.json"), project, "parch:implementer"
    )
    assert result.code == 0 and "ask" in result.stdout


@pytest.mark.parametrize("rel", ["tests/state/baseline.json", "tests/docs/GOAL.md"])
def test_tester_asks_the_owner_for_protected_files_inside_the_tests_folder(
    rel: str, project: Path
) -> None:
    """Раннего выхода нет: путь разрешён tester по месту (`tests/`), но защищённое имя файла остаётся
    под подтверждением владельца."""
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, "parch:tester")
    assert result.code == 0 and '"permissionDecision": "ask"' in result.stdout, result.stdout


def test_the_other_roles_keep_their_rules_after_the_tester_rule(project: Path) -> None:
    """Правило tester не задело остальных: `implementer` пишет код, но не тесты; основная сессия пишет код."""
    code = project / "src" / "convert.py"
    assert (
        run_hook("guard_paths.py", file_call("Write", code), project, "parch:implementer").code == 0
    )
    assert run_hook("guard_paths.py", file_call("Write", code), project, None).code == 0
    test = project / "tests" / "test_convert.py"
    assert run_hook(
        "guard_paths.py", file_call("Write", test), project, "parch:implementer"
    ).blocked


# ---------- правило «код правят только роли implementer» (F25, п. 1.3) ----------

CODE_ONLY_YES = "Код правят только роли implementer: да"
CODE_ONLY_NO = "Код правят только роли implementer: нет"
CODE_PATHS = [
    "plugin/hooks/x.py",
    "plugin/templates/ci/parch/x.py",
    "plugin/skills/x/scripts/x.py",
    "src/x.py",
]
NOT_CODE_PATHS = [
    "plugin/agents/x.md",
    "plugin/skills/x/SKILL.md",
    "plugin/.claude-plugin/plugin.json",
    "docs/x.md",
]
CODE_ONLY_MSG = "только роли implementer"


def code_only_project(project: Path, line: str | None = CODE_ONLY_YES) -> Path:
    """Проект с `[tool.parch] source_roots` и (если задана) строкой правила в CONSTITUTION.md."""
    (project / "pyproject.toml").write_text(
        '[tool.parch]\nsource_roots = ["plugin/hooks", "plugin/templates/ci/parch", '
        '"plugin/skills", "scripts"]\n',
        encoding="utf-8",
    )
    if line is not None:
        const = project / "docs" / "CONSTITUTION.md"
        const.write_text(
            const.read_text(encoding="utf-8") + f"\n## Кто правит код\n\n{line}\n", encoding="utf-8"
        )
    return project


@pytest.mark.parametrize("rel", CODE_PATHS)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_code_only_rule_blocks_the_main_session_on_code(rel: str, tool: str, project: Path) -> None:
    """Плохой пример: основная сессия без роли правит код проекта со строкой «да»: блок по-русски с путём."""
    code_only_project(project)
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, None)
    shown = result.stdout + result.stderr
    assert result.blocked, (rel, shown)
    assert CODE_ONLY_MSG in shown and rel in shown, shown


@pytest.mark.parametrize("rel", NOT_CODE_PATHS)
@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_code_only_rule_does_not_touch_documents_and_manifests(
    rel: str, tool: str, project: Path
) -> None:
    """Документы, SKILL.md и plugin.json кодом не считаются: правило их не блокирует. Для plugin.json охрана
    путей может спросить владельца по другой причине (защищённый путь); важно, что это не блок
    «только роли implementer»."""
    code_only_project(project)
    result = run_hook("guard_paths.py", file_call(tool, project / rel), project, None)
    shown = result.stdout + result.stderr
    assert result.code == 0 and not result.blocked, (rel, shown)
    assert CODE_ONLY_MSG not in shown, (rel, shown)


@pytest.mark.parametrize("line", [None, CODE_ONLY_NO], ids=["no-line", "line-no"])
@pytest.mark.parametrize("rel", CODE_PATHS)
def test_code_only_rule_is_off_without_the_yes_line(
    rel: str, line: str | None, project: Path
) -> None:
    """Без строки (фикстура `project`) и со строкой «нет» код правят все, как раньше."""
    code_only_project(project, line)
    result = run_hook("guard_paths.py", file_call("Write", project / rel), project, None)
    assert result.code == 0 and not result.blocked, (rel, result.stdout + result.stderr)
    assert CODE_ONLY_MSG not in result.stdout + result.stderr


@pytest.mark.parametrize("rel", CODE_PATHS)
def test_code_only_rule_lets_the_implementer_write_code(rel: str, project: Path) -> None:
    code_only_project(project)
    result = run_hook(
        "guard_paths.py", file_call("Write", project / rel), project, "parch:implementer"
    )
    assert result.code == 0 and not result.blocked, (rel, result.stdout + result.stderr)


def test_code_only_rule_still_blocks_tester_on_code_with_its_own_rule(project: Path) -> None:
    code_only_project(project)
    result = run_hook(
        "guard_paths.py", file_call("Write", project / "src" / "x.py"), project, "parch:tester"
    )
    assert result.blocked and "Роль tester правит только тесты" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "Код правят только роли implementer: нет",
        "Правило: Код правят только роли implementer: да, но не всегда.",
        "Заметка. Код правят только роли implementer: да",  # слово в середине строки
        "Код правят только роли implementer: да, кроме скриптов",
        "Код правят только роли implementer",
    ],
)
def test_code_only_rule_needs_the_exact_line(text: str, project: Path) -> None:
    """Плохой пример: строка в другом виде или слово в середине абзаца правило не включает."""
    code_only_project(project, text)
    result = run_hook("guard_paths.py", file_call("Write", project / "src" / "x.py"), project, None)
    assert result.code == 0 and not result.blocked, (text, result.stdout + result.stderr)


def set_roots(project: Path, toml: str | None) -> Path:
    """Проект со строкой «да» и заданным текстом pyproject.toml (None: файла нет)."""
    code_only_project(project)
    pyproject = project / "pyproject.toml"
    if toml is None:
        pyproject.unlink()
    else:
        pyproject.write_text(toml, encoding="utf-8")
    return project


def shown_of(result: object) -> str:
    return getattr(result, "stdout", "") + getattr(result, "stderr", "")


def test_code_only_rule_survives_a_broken_pyproject(project: Path) -> None:
    """Плохой пример: невалидный TOML. Охрана не падает (код выхода не «ошибка hook») и не разрешает всё."""
    set_roots(project, "[tool.parch\nsource_roots = = [\n")
    result = run_hook("guard_paths.py", file_call("Write", project / "src" / "x.py"), project, None)
    assert result.blocked, shown_of(result)
    assert CODE_ONLY_MSG in shown_of(result), shown_of(result)
    assert "Traceback" not in shown_of(result), shown_of(result)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_code_only_rule_blocks_an_uppercase_extension(tool: str, project: Path) -> None:
    """Плохой пример: `src/X.PY` на Windows тот же файл `x.py`, регистр расширения не обход."""
    code_only_project(project)
    result = run_hook("guard_paths.py", file_call(tool, project / "src" / "X.PY"), project, None)
    assert result.blocked and CODE_ONLY_MSG in shown_of(result), shown_of(result)


@pytest.mark.parametrize(
    "root",
    ["plugin\\\\hooks", "./plugin/hooks", ".\\\\plugin\\\\hooks"],
    ids=["backslash", "dot", "dot-backslash"],
)
def test_code_only_rule_understands_source_roots_written_with_backslashes_or_dot(
    root: str, project: Path
) -> None:
    set_roots(project, f'[tool.parch]\nsource_roots = ["{root}"]\n')
    result = run_hook(
        "guard_paths.py", file_call("Write", project / "plugin" / "hooks" / "x.py"), project, None
    )
    assert result.blocked and CODE_ONLY_MSG in shown_of(result), (root, shown_of(result))


@pytest.mark.parametrize("toml", [None, "[tool.parch]\n"], ids=["no-pyproject", "no-source-roots"])
def test_code_only_rule_treats_a_flat_project_as_all_code(toml: str | None, project: Path) -> None:
    """Нет source_roots и нет папки src/: кодом считается весь проект, документы нет."""
    set_roots(project, toml)
    for rel in ["app.py", "pkg/mod.py"]:
        result = run_hook("guard_paths.py", file_call("Write", project / rel), project, None)
        assert result.blocked and CODE_ONLY_MSG in shown_of(result), (rel, shown_of(result))
    readme = run_hook("guard_paths.py", file_call("Write", project / "README.md"), project, None)
    assert readme.code == 0 and not readme.blocked, shown_of(readme)


TEST_PATHS_IN_CODE = ["src/pkg/tests/test_x.py", "src/pkg/test_y.py", "src/pkg/z_test.py"]


@pytest.mark.parametrize(
    "toml", ['[tool.parch]\nsource_roots = ["."]\n', None], ids=["dot", "default"]
)
@pytest.mark.parametrize("rel", TEST_PATHS_IN_CODE)
def test_test_paths_inside_the_code_root_belong_to_the_test_rule(
    rel: str, toml: str | None, project: Path
) -> None:
    """Тестовый путь внутри корня кода: tester пишет, основная сессия получает блок правила тестов, не кода."""
    set_roots(project, toml if toml is not None else '[tool.parch]\nsource_roots = ["src"]\n')
    tester = run_hook("guard_paths.py", file_call("Write", project / rel), project, "parch:tester")
    assert tester.code == 0 and not tester.blocked, (rel, shown_of(tester))
    assert CODE_ONLY_MSG not in shown_of(tester)
    main = run_hook("guard_paths.py", file_call("Write", project / rel), project, None)
    assert main.blocked, (rel, shown_of(main))
    assert "architect и tester" in shown_of(main), shown_of(main)
    assert CODE_ONLY_MSG not in shown_of(main), shown_of(main)


@pytest.mark.parametrize(
    "toml", ['[tool.parch]\nsource_roots = ["."]\n', None], ids=["dot", "default"]
)
def test_tester_is_still_blocked_on_ordinary_code_inside_the_code_root(
    toml: str | None, project: Path
) -> None:
    set_roots(project, toml if toml is not None else '[tool.parch]\nsource_roots = ["src"]\n')
    result = run_hook(
        "guard_paths.py",
        file_call("Write", project / "src" / "pkg" / "mod.py"),
        project,
        "parch:tester",
    )
    assert result.blocked and "Роль tester правит только тесты" in shown_of(result), shown_of(
        result
    )


def normalized_roots(roots: list[str]) -> set[str]:
    return {r.replace("\\", "/").removeprefix("./").strip("/").lower() or "." for r in roots}


@pytest.mark.parametrize(
    ("toml", "src_dir"),
    [
        ('[tool.parch]\nsource_roots = ["plugin/hooks", "scripts"]\n', False),
        ("[tool.parch]\n", True),
        (None, False),
        ('[tool.parch]\nsource_roots = ["plugin\\\\hooks", "./lib"]\n', False),
    ],
    ids=["configured", "default-src", "default-flat", "backslashes"],
)
def test_hook_code_roots_cover_the_roots_ci_uses(
    toml: str | None, src_dir: bool, project: Path
) -> None:
    """Умолчания охраны путей совпадают с `py_source_roots` в CI: корень, который проверяет CI, охрана не пропустит."""
    import guard_paths
    import parch_ci

    set_roots(project, toml)
    if src_dir:
        (project / "src").mkdir()
    ci_roots = normalized_roots(parch_ci.py_source_roots(project))
    hook_roots = set(guard_paths._code_roots(project))  # pyright: ignore[reportPrivateUsage]
    assert ci_roots <= hook_roots, (ci_roots, hook_roots)
    assert "src" in hook_roots, hook_roots


def test_the_constitution_template_has_the_code_only_line_set_to_no_exactly_once() -> None:
    """Чужие проекты работают как раньше: в шаблоне правило выключено, строка одна."""
    text = (ROOT / "plugin" / "templates" / "docs" / "CONSTITUTION.md").read_text(encoding="utf-8")
    assert text.count(CODE_ONLY_NO) == 1
    assert "Код правят только роли implementer: да" not in text


# ---------- согласованность ролей ----------


def roles_in_text(text: str) -> set[str]:
    """Роли, на которые ссылается текст: слова-кандидаты, «роль X», «роли X и Y», `*_ROLES = frozenset({...})`
    (`parch:X` не берём: так называются и агенты, и навыки)."""
    found = {m.lower() for m in re.findall(rf"\b({CANDIDATES})\b", text, re.IGNORECASE)}
    found |= {m.lower() for m in re.findall(r"\bрол[ьи]\s+[`«]?([a-z][a-z-]+)", text)}
    for group in re.findall(r"\bроли\s+[`«]?([a-z][a-z-]+)[`»]?\s+и\s+[`«]?([a-z][a-z-]+)", text):
        found |= set(group)
    for body in re.findall(r"_ROLES\s*=\s*frozenset\(\{([^}]*)\}\)", text):
        found |= set(re.findall(r'"([a-z][a-z-]+)"', body))
    return {role for role in found if role not in {"role", "roles"}}


def unimplemented(text: str, agents: set[str], marked: set[str]) -> set[str]:
    """Роли, упомянутые в тексте, которых нет ни среди агентов, ни в помеченных «пока не реализована»."""
    return roles_in_text(text) - agents - marked


def marked_roles(backlog: str) -> set[str]:
    lines = [line for line in backlog.splitlines() if MARKER in line]
    assert len(lines) == 1, (
        f"строка «{MARKER}» должна быть в BACKLOG ровно одна, найдено {len(lines)}"
    )
    tail = lines[0].split(":", 1)[1] if ":" in lines[0] else ""
    names = tail.split(".")[0]
    return {w.strip(" `") for w in names.split(",") if w.strip(" `")}


def scope_files() -> list[Path]:
    files = sorted((ROOT / "plugin" / "hooks").glob("*.py"))
    files += sorted((ROOT / "plugin" / "templates").rglob("*.md"))
    files += sorted((ROOT / "plugin" / "skills").rglob("SKILL.md"))
    files += sorted(AGENTS.glob("*.md"))
    files.append(ROOT / "docs" / "STANDARD.md")
    return files


def test_every_role_the_rules_mention_exists_or_is_marked_as_not_implemented() -> None:
    agents, marked = agent_names(), marked_roles(BACKLOG.read_text(encoding="utf-8"))
    problems = {
        path.relative_to(ROOT).as_posix(): sorted(
            unimplemented(path.read_text(encoding="utf-8"), agents, marked)
        )
        for path in scope_files()
    }
    problems = {path: roles for path, roles in problems.items() if roles}
    assert problems == {}, (
        f"правила ссылаются на роли без агента и без пометки в BACKLOG: {problems}"
    )


def test_a_rule_that_names_a_missing_role_is_caught() -> None:
    """Плохие примеры на подставном тексте: правило со ссылкой на несуществующую роль красное."""
    agents, marked = {"implementer", "reviewer", "tester"}, {"janitor"}
    assert unimplemented("Тесты правят только роли architect и tester.", agents, marked) == {
        "architect"
    }
    assert unimplemented('TEST_ROLES = frozenset({"architect", "tester"})', agents, marked) == {
        "architect"
    }
    assert unimplemented("Этот шаг делает роль `inspector`.", agents, marked) == {"inspector"}
    assert unimplemented("Агент parch:planner разбирает план.", agents, marked) == {"planner"}
    assert (
        unimplemented("Уборку делает janitor, тесты пишет tester.", agents, marked) == set()
    )  # janitor помечена


def test_the_marker_line_exists_once_and_names_only_roles_without_an_agent() -> None:
    """Пометка не устаревает: роль, у которой появился агент, из неё убирается."""
    marked = marked_roles(BACKLOG.read_text(encoding="utf-8"))
    assert marked, "в пометке нет ни одной роли"
    assert marked.isdisjoint(agent_names()), (
        f"у ролей {sorted(marked & agent_names())} уже есть агент: уберите их из пометки"
    )
    assert {
        "architect",
        "janitor",
        "explorer",
    } <= marked  # на 2026-10-07: этих агентов нет, правила на них ссылаются


@pytest.mark.parametrize("name", ["implementer", "reviewer", "tester"])
def test_every_live_role_has_a_well_formed_agent_file(name: str) -> None:
    fields = frontmatter(AGENTS / f"{name}.md")
    assert fields["name"] == name and fields["description"]
