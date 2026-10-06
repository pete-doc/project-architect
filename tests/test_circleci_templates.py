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


def test_a_final_check_that_skips_a_language_is_caught() -> None:
    """Плохой пример: итоговый `check` без одного языка пропустил бы красный язык, а слияние шло бы по зелёному `check`."""
    config = render(["python", "typescript"]).replace("            - check-typescript\n", "")
    assert requires_of_check(config) == ["check-python"]
    assert "check-typescript" in re.findall(r"^  (check-[\w-]+):\n    docker:", config, re.M)
    assert set(re.findall(r"^  (check-[\w-]+):\n    docker:", config, re.M)) != set(
        requires_of_check(config)
    )


def test_the_ci_workflow_does_not_run_on_main_and_the_status_branch() -> None:
    """Ветка `status` получает табло от задания state; без исключения `ci` шёл бы на ней после каждого слияния."""
    config = render(["python"])
    workflows = config.split("\nworkflows:\n", 1)[1]
    assert "equal: [main, << pipeline.git.branch >>]" in workflows
    assert "equal: [status, << pipeline.git.branch >>]" in workflows
    bad = workflows.replace(
        "equal: [status, << pipeline.git.branch >>]", "equal: [dev, << pipeline.git.branch >>]"
    )
    assert (
        "equal: [status, << pipeline.git.branch >>]" not in bad
    )  # плохой пример: исключения status нет


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
