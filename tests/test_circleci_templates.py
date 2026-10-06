# ruff: noqa: E501
"""Шаблоны CI проектов пользователей на CircleCI (блок F24, PR 1; ADR-0022, docs/specs/F24-circleci-templates.md).

Живой запуск на CircleCI тестами не заменить (его делает прогон F20), поэтому здесь проверяется то, что
проверить можно: что собирает init, итоговый `check` требует все языки, ветки `main` и `status` исключены,
шаг `fetch-main` даёт `git log main..HEAD` на «урезанном» checkout, «Основания» берутся из коммитов ветки.
У каждой проверки есть заведомо плохой пример.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from conftest import INIT_ANSWERS

REPO = Path(__file__).resolve().parent.parent
CIRCLE = REPO / "plugin" / "templates" / "ci" / "circleci"
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
LANGUAGES = ("python", "typescript", "csharp", "powershell")
GOOD_BASIS = (
    "Добавил перевод метров в футы\n\n## Основания\n\n"
    'Прочитал docs/MODULES.md и каталог; поиск по коду "convert" ничего не нашёл; git log -S"feet" пуст.\n'
)


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass в init_project ищет свой модуль здесь
    spec.loader.exec_module(module)
    return module


init_module: Any = load(INIT, "init_project_circleci_templates")
parch_ci: Any = load(SCRIPT, "parch_ci_circleci_templates")


def render(languages: list[str]) -> str:
    return str(init_module.render_circleci(languages))


def run_init(project: Path, languages: list[str]) -> None:
    request = {
        "project_dir": str(project),
        "name": "Мой сервис",
        "languages": languages,
        "description": "Считает заказы.",
        "priorities": "надёжность",
        **INIT_ANSWERS,
    }
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env=os.environ,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")


# ---------- что собирает init ----------


@pytest.mark.parametrize("language", LANGUAGES)
def test_init_builds_one_circleci_config_and_no_github_actions(
    tmp_path: Path, language: str
) -> None:
    run_init(tmp_path, [language])
    config = (tmp_path / ".circleci" / "config.yml").read_text(encoding="utf-8")
    assert f"  check-{language}:\n" in config and "\n  check:\n" in config
    assert "workflows:\n  ci:\n" in config
    assert not (
        tmp_path / ".github" / "workflows"
    ).exists()  # GitHub Actions в шаблонах нет (ADR-0022)
    assert (
        tmp_path / ".github" / "parch" / "parch_ci.py"
    ).is_file()  # скрипты проверок остались там же


def requires_of_check(config: str) -> list[str]:
    match = re.search(
        r"^      - check:\n          requires:\n((?:            - [\w-]+\n)+)", config, re.M
    )
    assert match is not None, "у итогового check нет requires"
    return [line.strip()[2:] for line in match[1].splitlines()]


def test_the_final_check_requires_every_language_job() -> None:
    config = render(list(LANGUAGES))
    assert requires_of_check(config) == [f"check-{lang}" for lang in LANGUAGES]
    jobs = re.findall(r"^  (check-[\w-]+):\n    docker:", config, re.M)
    assert jobs == [f"check-{lang}" for lang in LANGUAGES]


def standard_config_problems(tmp_path: Path, config: str) -> list[str]:
    """Что скажет `standard` про собранный конфиг: правила CircleCI из parch_ci."""
    path = tmp_path / "config.yml"
    path.write_text(config, encoding="utf-8", newline="\n")
    problems: list[str] = parch_ci.circleci_problems(path, "")
    return problems


def test_a_final_check_that_skips_a_language_is_caught(tmp_path: Path) -> None:
    """`standard` требует `requires` на все `check-<язык>` из конфига: настоящий конфиг init проходит, без языка красный."""
    config = render(list(LANGUAGES))
    assert standard_config_problems(tmp_path, config) == []
    for lang in LANGUAGES:  # плохой пример для каждого языка: итоговый check не требует его
        broken = config.replace(f"            - check-{lang}\n", "")
        problems = standard_config_problems(tmp_path, broken)
        assert any(f"не требует check-{lang}" in line for line in problems), lang
    headless = config.split("      - check:\n          requires:\n")[
        0
    ]  # итогового check в workflow нет вовсе
    assert any(
        "нет итогового задания check" in line
        for line in standard_config_problems(tmp_path, headless)
    )


def test_standard_stops_the_whole_project_when_the_final_check_misses_a_language(
    tmp_path: Path,
) -> None:
    """То же через настоящую команду `parch_ci.py standard` на настоящем проекте init: код выхода 1."""
    project = project_with_commits(tmp_path, [GOOD_BASIS])
    assert standard(project).returncode == 0  # собранный init конфиг и «Основания» в порядке
    path = project / ".circleci" / "config.yml"
    config = path.read_text(encoding="utf-8")
    path.write_text(
        config.replace("            - check-python\n", ""), encoding="utf-8", newline="\n"
    )
    done = standard(project)
    assert (
        done.returncode == 1
        and "нет итогового задания check с requires на check-python" in done.stdout
    )


def runs_on(config: str, branch: str) -> bool:
    """Запустится ли workflow `ci` на ветке: считает выражение `when` настоящего конфига (`not: equal: [ветка, << pipeline.git.branch >>]`)."""
    workflows = config.split("\nworkflows:\n", 1)[1]
    when = workflows.split("    jobs:\n", 1)[0]
    excluded = re.findall(r"equal: \[([\w./-]+), << pipeline\.git\.branch >>\]", when)
    return branch not in excluded


def test_the_ci_workflow_does_not_run_on_main_and_the_status_branch() -> None:
    """Ветка `status` получает табло от задания state; без исключения `ci` шёл бы на ней после каждого слияния."""
    for languages in ([lang] for lang in LANGUAGES):
        config = render(languages)
        assert not runs_on(config, "main") and not runs_on(config, "status")
        assert runs_on(config, "feature") and runs_on(config, "F24-x")  # обычные ветки идут
    # плохой пример: конфиг, где исключена только main (так выглядел бы шаблон без правки по ревью)
    without_status = render(["python"]).replace(
        "        - not:\n            equal: [status, << pipeline.git.branch >>]\n", ""
    )
    assert runs_on(without_status, "status") and not runs_on(without_status, "main")


def run_steps_without_limit(raw: str) -> list[str]:
    """Шаги `run` без `no_output_timeout` и шаги тестов без `timeout` (короткая запись `- run: команда` предела не даёт)."""
    found: list[str] = []
    for step in re.split(r"\n(?= +- )", raw):
        head = step.lstrip().split("\n", 1)[0]
        if not head.startswith("- run:"):
            continue
        if "no_output_timeout:" not in step:
            found.append(step.strip().splitlines()[0])
        elif re.search(r"\b(pytest|dotnet test|npm test)\b", step) and "timeout " not in step:
            found.append(step.strip().splitlines()[0] + " (тесты без timeout)")
    return found


def test_every_run_step_of_the_templates_has_a_time_limit() -> None:
    """Пределы времени: у каждого шага run в шаблонах есть `no_output_timeout`, у тестов ещё и `timeout`."""
    for path in sorted(CIRCLE.glob("*.yml")):
        assert run_steps_without_limit(path.read_text(encoding="utf-8")) == [], path.name
    short = "    steps:\n      - run: ruff check .\n      - run:\n          name: тесты\n          no_output_timeout: 5m\n          command: pytest\n"
    assert run_steps_without_limit(short) == [
        "- run: ruff check .",
        "- run: (тесты без timeout)",
    ]  # плохие примеры: короткая запись без предела и тесты только с no_output_timeout


PLAIN_COLON = re.compile(r"^\s*(?:- )?[\w-]+: (?![\"'|>\[{&*!%@`])[^#\n]*: ")


def plain_scalar_problems(config: str) -> list[str]:
    """Строки `ключ: значение`, где в обычной (не в кавычках) строке есть «: »: YAML разберёт их как вложенный ключ и упадёт."""
    return [line for line in config.splitlines() if PLAIN_COLON.match(line)]


def test_the_generated_config_has_no_plain_scalars_with_a_colon() -> None:
    assert plain_scalar_problems(render(list(LANGUAGES))) == []
    bad = "description: Ветка main и история: нужны проверке\n"  # ровно эта ошибка нашлась при сборке шаблона
    assert plain_scalar_problems(bad) == [bad.strip()]
    assert plain_scalar_problems('description: "в кавычках: можно"\n') == []


def test_templates_follow_the_cost_rules_of_standard(tmp_path: Path) -> None:
    """`standard` читает собранный конфиг: Linux, класс не выше medium, пределы времени у долгих шагов."""
    project = tmp_path / "p"
    (project / ".circleci").mkdir(parents=True)
    path = project / ".circleci" / "config.yml"
    good = render(list(LANGUAGES))
    path.write_text(good, encoding="utf-8")
    assert parch_ci.circleci_problems(path, "") == []
    for broken, expect in (
        (
            good.replace("resource_class: medium", "resource_class: large", 1),
            "класс ресурсов large",
        ),
        (
            good.replace(
                "  check-python:\n    docker:\n      - image: cimg/python:3.12\n",
                "  check-python:\n    executor: win/server-2022\n",
                1,
            ),
            "win/server-2022",
        ),
        (
            good.replace(
                "no_output_timeout: 10m\n          command: timeout 600 pytest",
                "command: pytest",
                1,
            ),
            "без предела времени",
        ),
    ):  # плохие примеры
        path.write_text(broken, encoding="utf-8")
        assert any(expect in line for line in parch_ci.circleci_problems(path, "")), expect


# ---------- checkout CircleCI и git log main..HEAD ----------


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    return done.stdout


def fetch_main_script() -> str:
    """Команда шага `fetch-main` из head.yml, готовая к запуску в bash."""
    lines = (CIRCLE / "head.yml").read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if "name: Подтянуть main и историю ветки" in line
    )
    begin = next(i for i in range(start, len(lines)) if lines[i].strip() == "command: |") + 1
    body: list[str] = []
    for line in lines[begin:]:
        if line.strip() and not line.startswith(" " * 12):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def branch_checkout(tmp_path: Path, messages: list[str]) -> Path:
    """Как checkout CircleCI: «сервер» с main и веткой PR, клон только ветки PR и только последнего коммита."""
    origin, seed, clone = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "clone"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("x\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    git(seed, "checkout", "-q", "-b", "feature")
    for message in messages:
        git(seed, "commit", "-q", "--allow-empty", "-m", message)
    git(seed, "push", "-q", "origin", "feature")
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth",
        "1",
        "--single-branch",
        "--branch",
        "feature",
        origin.as_uri(),
        str(clone),
    )
    return clone


def test_fetch_main_gives_the_branch_history_that_a_shallow_checkout_lacks(tmp_path: Path) -> None:
    clone = branch_checkout(tmp_path, ["первый", GOOD_BASIS])
    bash = shutil.which("bash")
    assert bash, "для проверки шага fetch-main нужен bash"
    before = subprocess.run(
        ["git", "log", "origin/main..HEAD"], cwd=clone, capture_output=True, text=True, check=False
    )
    assert (
        before.returncode != 0
    )  # плохой пример: без шага checkout не знает origin/main, `git log main..HEAD` не работает
    done = subprocess.run(
        [bash, "-c", fetch_main_script()],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    log = git(clone, "log", "--format=%s", "origin/main..HEAD").split("\n")
    assert [line for line in log if line] == [
        "Добавил перевод метров в футы",
        "первый",
    ]  # обе коммита ветки


# ---------- «Основания» из сообщений коммитов ветки ----------


def project_with_commits(tmp_path: Path, messages: list[str]) -> Path:
    """Подключённый проект: ветка main записана как origin/main, поверх неё коммиты с заданными сообщениями."""
    run_init(tmp_path, ["python"])
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    for message in messages:
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", message)
    return tmp_path


def standard(project: Path, ref: str = "origin/main") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(project / ".github" / "parch" / "parch_ci.py"), "standard"],
        cwd=project, capture_output=True, text=True, encoding="utf-8", check=False, timeout=300,
        env={**os.environ, "PARCH_BASE_REF": ref},
    )  # fmt: skip


def test_the_basis_in_one_commit_of_the_branch_is_enough(tmp_path: Path) -> None:
    project = project_with_commits(
        tmp_path, ["первый без раздела", GOOD_BASIS, "третий без раздела"]
    )
    done = standard(project)
    assert done.returncode == 0, (
        done.stdout + done.stderr
    )  # раздел в одном коммите из трёх, не в каждом


def test_a_branch_without_the_basis_section_is_red(tmp_path: Path) -> None:
    project = project_with_commits(tmp_path, ["правка один", "правка два"])
    done = standard(project)
    assert done.returncode == 1
    assert "Основания в PR" in done.stdout and "ни в одном из 2 коммитов ветки" in done.stdout
    assert "нет раздела «Основания»" in done.stdout


@pytest.mark.parametrize("body", ["", "\n- \n", "---\n", "— — —\n\n_\n"])
def test_an_empty_basis_section_or_only_dashes_is_red(tmp_path: Path, body: str) -> None:
    project = project_with_commits(tmp_path, [f"правка\n\n## Основания\n\n{body}"])
    done = standard(project)
    assert done.returncode == 1
    assert "пуст или из одних прочерков" in done.stdout


def test_a_branch_without_new_commits_is_red(tmp_path: Path) -> None:
    project = project_with_commits(tmp_path, [])
    done = standard(project)
    assert done.returncode == 1
    assert "в ветке нет новых коммитов относительно основной" in done.stdout


def test_missing_history_of_main_is_red_with_a_clear_message(tmp_path: Path) -> None:
    project = project_with_commits(tmp_path, [GOOD_BASIS])
    done = standard(project, ref="origin/нет-такой-ветки")
    assert done.returncode == 1
    assert (
        "история ветки от origin/нет-такой-ветки недоступна" in done.stdout
        and "fetch-main" in done.stdout
    )


def test_the_basis_command_prints_the_section_for_the_pull_request_text(tmp_path: Path) -> None:
    """Раздел пишется один раз, в коммите; в описание PR он берётся командой, а не переписывается."""
    project = project_with_commits(tmp_path, ["первый", GOOD_BASIS])
    env = {**os.environ, "PARCH_BASE_REF": "origin/main"}
    script = str(project / ".github" / "parch" / "parch_ci.py")
    done = subprocess.run(
        [sys.executable, script, "basis"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stdout
    assert "## Основания" in done.stdout and "Прочитал docs/MODULES.md" in done.stdout
    (tmp_path / "second").mkdir()
    empty = project_with_commits(tmp_path / "second", ["без раздела"])
    failed = subprocess.run(
        [sys.executable, str(empty / ".github" / "parch" / "parch_ci.py"), "basis"],
        cwd=empty,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    assert failed.returncode == 1


def test_the_old_pull_request_body_route_still_works_for_projects_on_actions() -> None:
    standard_module: Any = load(
        REPO / "plugin" / "templates" / "ci" / "parch" / "parch_standard.py",
        "parch_standard_templates_test",
    )
    assert standard_module.basis_problem("## Основания\n\nпоиск по коду: нашёл\n") is None
    assert "нет раздела" in standard_module.basis_problem("## Что сделано\n\nтекст\n")


# ---------- «Основания» пишутся один раз ----------


def test_the_executor_instructions_take_the_basis_from_the_commit() -> None:
    """Где сказано, что раздел пишется один раз, в коммите, а в описание PR идёт командой `basis`."""
    for rel in (
        "plugin/templates/github/pull_request_template.md",
        "plugin/agents/implementer.md",
        "plugin/templates/docs/CONSTITUTION.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "parch_ci.py basis" in text, rel
        assert "коммит" in text, rel
    for rel in ("plugin/templates/github/pull_request_template.md", "plugin/agents/implementer.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "Основания:" in text and "без решётки" in text, (
            rel
        )  # вариант для коммита из редактора


# ---------- «Основания:» без решётки: git в редакторе вырезает строки с # ----------

standard_module: Any = load(
    REPO / "plugin" / "templates" / "ci" / "parch" / "parch_standard.py",
    "parch_standard_circleci_templates",
)
BASIS_TEXT = 'Прочитал каталог и реестр модулей; поиск по коду "convert" ничего не нашёл; git log -S"feet" пуст.'


@pytest.mark.parametrize(
    "message",
    [
        f"правка\n\n## Основания\n\n{BASIS_TEXT}\n",
        f"правка\n\nОснования: {BASIS_TEXT}\n",
        f"правка\n\nОснования:\n- {BASIS_TEXT}\n- ещё строка\n\nПроверка:\nтесты зелёные\n",
        f"правка\n\nоснования:\n{BASIS_TEXT}\n",
        # строки вида «Поиск по коду:» внутри раздела его не обрывают (замечание 1 четвёртого ревью)
        f"правка\n\nОснования:\nПоиск по коду:\n- convert: не нашёл\nИстория git:\n- git log -S feet: пусто\nКаталог:\n- {BASIS_TEXT}\n",
        f"правка\n\nОснования:\n\n{BASIS_TEXT}\n",  # одна пустая строка после «Основания:» не конец раздела
    ],
)
def test_both_heading_variants_pass(message: str) -> None:
    assert standard_module.basis_problem(message, "в коммите") is None


@pytest.mark.parametrize(
    "message",
    [
        "правка\n\n## Основания\n\n",
        "правка\n\nОснования:\n",
        "правка\n\nОснования:\n---\n",
        "правка\n\nОснования:\n- - -\n\n\nПроверка:\nпоиск по коду\n",  # две пустые строки кончают раздел: текст после него не засчитывается
        "правка\n\nОснования:\n---\n## Проверка\nпоиск по коду\n",  # заголовок с # кончает раздел
        "правка\n\nОснования:\n\n\nпоиск по коду\n",  # пустая строка, ещё пустая: раздел пуст
        "правка\n\n## Основания\n\n— — —\n",
        "правка\n\nбез раздела вовсе\n",
    ],
)
def test_empty_basis_dashes_or_no_section_stay_red_in_both_variants(message: str) -> None:
    assert standard_module.basis_problem(message, "в коммите") is not None


def test_the_section_survives_the_lines_git_strips_from_an_editor_message(tmp_path: Path) -> None:
    """Git при коммите из редактора (`--cleanup=strip`) вырезает строки с #: вариант «Основания:» остаётся, «## Основания» теряется."""
    editor_text = "правка\n\n# комментарий git\n"
    with_label = project_with_commits(tmp_path / "a", [])
    git(
        with_label,
        "commit",
        "-q",
        "--allow-empty",
        "--cleanup=strip",
        "-m",
        f"{editor_text}\nОснования: {BASIS_TEXT}\n# ещё комментарий",
    )
    assert standard(with_label).returncode == 0
    with_hash = project_with_commits(tmp_path / "b", [])
    git(
        with_hash,
        "commit",
        "-q",
        "--allow-empty",
        "--cleanup=strip",
        "-m",
        f"{editor_text}\n## Основания\n\n{BASIS_TEXT}\n",
    )
    lost = standard(with_hash)
    assert (
        lost.returncode == 1 and "нет раздела «Основания»" in lost.stdout
    )  # плохой пример: решётка пропала вместе с заголовком


# ---------- fetch-main: сбой докачки роняет шаг ----------


def test_a_failed_history_fetch_turns_the_step_red_with_a_message(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash, "для проверки шага fetch-main нужен bash"
    fake = tmp_path / "bin"
    fake.mkdir()
    stub = fake / "git"
    stub.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *is-shallow-repository*) echo true ;;\n"
        '  *fetch*) echo "fatal: сеть недоступна" >&2; exit 128 ;;\n'
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": fake.as_posix() + os.pathsep + os.environ.get("PATH", "")}
    done = subprocess.run(
        [bash, "-c", fetch_main_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    assert done.returncode == 1
    assert (
        "не удалось докачать историю ветки" in done.stdout
    )  # понятное сообщение по-русски, а не молчаливый зелёный
    assert "|| true" not in fetch_main_script()


# ---------- init: уже существующий .circleci/config.yml ----------


def test_an_existing_circleci_config_is_reported_and_the_owner_steps_are_not_shown(
    tmp_path: Path,
) -> None:
    (tmp_path / ".circleci").mkdir(parents=True)
    (tmp_path / ".circleci" / "config.yml").write_text("version: 2.1\n", encoding="utf-8")
    request = {
        "project_dir": str(tmp_path),
        "name": "Мой сервис",
        "languages": ["python", "typescript"],
        "description": "Считает заказы.",
        "priorities": "надёжность",
        **INIT_ANSWERS,
    }
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env=os.environ,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    result = json.loads(done.stdout.decode("utf-8"))
    notes: list[str] = result["notes"]
    assert ".circleci/config.yml" in result["skipped_existing"]
    assert (tmp_path / ".circleci" / "config.yml").read_text(
        encoding="utf-8"
    ) == "version: 2.1\n"  # не перезаписан
    existing = next(n for n in notes if n.startswith("Файл .circleci/config.yml уже был"))
    assert "check-python, check-typescript и check не добавлены" in existing
    assert "добавьте вручную или удалите файл и запустите init снова" in existing
    assert not any(
        n.startswith("CI работает на CircleCI") for n in notes
    )  # В1–В5 как «всё готово» не выводятся


# ---------- официальный валидатор CircleCI ----------


def circleci_cli() -> str:
    cli = shutil.which("circleci")
    assert cli, (
        "CLI CircleCI не найден в PATH (тест красный, не пропускается: храповик): поставьте circleci 1.2.0 с "
        "github.com/CircleCI-Public/circleci-cli/releases/tag/v1.2.0, SHA-256 linux_amd64.tar.gz "
        "9d8dcf9dc5c681127e053f26e32cc751822b786a77cbafa46145e6945919ecd0, windows_amd64.zip "
        "609c35e9f342be1e680f95aca3454abc8f0a2e6ad695361ce77fc24694cdeba7 (как шаг install-tools в .circleci/continue_config.yml)"
    )
    return cli


@pytest.mark.parametrize(
    "languages", [[lang] for lang in LANGUAGES] + [list(LANGUAGES)], ids=lambda x: "+".join(x)
)
def test_the_official_validator_accepts_every_config_that_init_builds(
    tmp_path: Path, languages: list[str]
) -> None:
    path = tmp_path / "config.yml"
    path.write_text(render(languages), encoding="utf-8", newline="\n")
    done = subprocess.run(
        [circleci_cli(), "config", "validate", "--skip-update-check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_official_validator_rejects_a_broken_config(tmp_path: Path) -> None:
    """Плохой пример: итоговый check требует несуществующее задание."""
    path = tmp_path / "config.yml"
    path.write_text(
        render(["python"]).replace("            - check-python\n", "            - check-nothing\n"),
        encoding="utf-8",
        newline="\n",
    )
    done = subprocess.run(
        [circleci_cli(), "config", "validate", "--skip-update-check", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert done.returncode != 0
