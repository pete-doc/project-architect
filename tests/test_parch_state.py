# ruff: noqa: E501
"""Табло в CircleCI (блок F24, PR 2; ADR-0022): итог тестов, пометка, публикация на ветку `status`, порядок ключа.

Три замечания по табло из BACKLOG и их решение:
1. проект без тестов: код 5 pytest при пустом baseline это успех (зелёное, табло пишет «в проекте нет тестов»);
2. нет отчёта при существующих тестах, как и упавшие тесты: задание красное, табло публикуется с пометкой;
3. порядок в задании state: тесты проекта, потом сборка табло, и только потом ключ записи (add_ssh_keys).
У каждой проверки заведомо плохой пример на подставных данных; публикация проверяется на настоящих временных
git-репозиториях («сервер» это bare-репозиторий).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
PARCH = REPO / "plugin" / "templates" / "ci" / "parch"
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(PARCH))
state: Any = load(PARCH / "parch_state.py", "parch_state_under_test")
init_module: Any = load(INIT, "init_project_parch_state")
parch_ci: Any = load(PARCH / "parch_ci.py", "parch_ci_parch_state")

JUNIT_OK = '<testsuites><testsuite name="p" tests="1"><testcase classname="t" name="test_one"/></testsuite></testsuites>'
JUNIT_FAIL = (
    '<testsuites><testsuite name="p" tests="2"><testcase classname="t" name="test_one"/>'
    '<testcase classname="t" name="test_two"><failure message="x"/></testcase></testsuite></testsuites>'
)


def project(root: Path, tests: list[str]) -> Path:
    (root / "state").mkdir(parents=True)
    baseline = {"version": 1, "tests": {"python": tests}}
    (root / "state" / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    return root


def report(root: Path, xml: str) -> Path:
    path = root / "test-report.xml"
    path.write_text(xml, encoding="utf-8")
    return path


# ---------- итог тестов (решение владельца 2026-10-06) ----------


def test_a_project_without_tests_is_green_and_the_board_says_so(tmp_path: Path) -> None:
    """Код 5 pytest (ничего не собрано) при пустом baseline это успех; отчёта может не быть вовсе."""
    root = project(tmp_path, [])
    for given in (None, report(root, '<testsuites><testsuite name="p" tests="0"/></testsuites>')):
        result = state.verdict(root, given, 5)
        assert (result.note, result.red) == ("в проекте нет тестов", False)
    assert state.verdict(root, None, 0).red is False  # нет отчёта и нет тестов: тоже не красное
    # плохой пример: тесты сломались до отчёта (ошибка импорта, ранний сбой), а baseline ещё пуст: это не «нет тестов»
    for code in (1, 2, 4, 124):
        broken = state.verdict(root, None, code)
        assert broken.red and f"кодом {code}" in broken.note, code


def test_no_report_while_tests_exist_is_red(tmp_path: Path) -> None:
    """Плохой пример: тесты в проекте есть (baseline), а отчёта нет: тесты не запустились, зелёным это быть не может."""
    root = project(tmp_path, ["tests/test_a.py::test_one"])
    result = state.verdict(root, None, 2)
    assert (
        result.red
        and result.note == "отчёта тестов нет при существующих тестах: тесты не запустились"
    )
    result = state.verdict(root, root / "нет-такого-отчёта.xml", 0)  # код 0, но файла отчёта нет
    assert result.red


def test_exit_code_5_with_tests_in_the_baseline_is_red(tmp_path: Path) -> None:
    """Плохой пример: pytest ничего не собрал (код 5), хотя baseline перечисляет тесты: они пропали."""
    root = project(tmp_path, ["tests/test_a.py::test_one"])
    result = state.verdict(
        root, report(root, '<testsuites><testsuite name="p" tests="0"/></testsuites>'), 5
    )
    assert result.red  # отчёт без тестов нечитаем: красное с пометкой, а не молчаливый зелёный
    no_report = state.verdict(
        root, None, 5
    )  # и без отчёта при тестах в baseline код 5 не делает зелёным
    assert no_report.red and "существующих тестах" in no_report.note


def test_failed_tests_are_red_with_their_number(tmp_path: Path) -> None:
    root = project(tmp_path, ["tests/test_a.py::test_one", "tests/test_a.py::test_two"])
    result = state.verdict(root, report(root, JUNIT_FAIL), 1)
    assert (result.note, result.red) == ("тесты упали: 1", True)


def test_green_tests_give_no_note_and_an_unreadable_report_is_red(tmp_path: Path) -> None:
    root = project(tmp_path, ["tests/test_a.py::test_one"])
    assert state.verdict(root, report(root, JUNIT_OK), 0) == state.Verdict("", False)
    broken = state.verdict(root, report(root, "это не отчёт"), 1)
    assert broken.red and "не читается" in broken.note


def test_the_note_goes_to_the_top_of_the_board_right_after_the_title() -> None:
    text = "# Прогресс: 1 из 2 блоков готово\n\n## Цели\n"
    marked = state.annotate(text, "тесты упали: 3")
    assert marked.splitlines()[:3] == [
        "# Прогресс: 1 из 2 блоков готово",
        "",
        "> **Тесты:** тесты упали: 3",
    ]
    assert state.annotate(text, "") == text  # всё хорошо: табло без пометки


def test_the_job_is_red_only_when_the_tests_are_red(tmp_path: Path) -> None:
    """`final` отдаёт код 1 для красного итога и 0 для зелёного; ключ и публикация на цвет не влияют."""
    script = str(PARCH / "parch_state.py")
    for body, code in (
        ({"note": "тесты упали: 1", "red": True}, 1),
        ({"note": "в проекте нет тестов", "red": False}, 0),
    ):
        verdict_file = tmp_path / f"v{code}.json"
        verdict_file.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, script, "final", "--verdict", str(verdict_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert done.returncode == code, done.stdout


def test_the_command_line_writes_the_verdict_and_prints_it(tmp_path: Path) -> None:
    root = project(tmp_path, ["tests/test_a.py::test_one"])
    out = tmp_path / "verdict.json"
    done = subprocess.run(
        [sys.executable, str(PARCH / "parch_state.py"), "verdict", "--project", str(root), "--report", str(root / "нет.xml"), "--exit-code", "1", "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )  # fmt: skip
    assert done.returncode == 0 and "отчёта тестов нет при существующих тестах" in done.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["red"] is True


# ---------- публикация на ветку status ----------


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8",
    )  # fmt: skip
    return done.stdout


def server(tmp_path: Path) -> tuple[Path, Path, str]:
    """«Сервер» (bare) с веткой main и клон проекта: (клон, путь сервера, коммит main)."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "README.md").write_text("x\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    return work, origin, git(work, "rev-parse", "HEAD").strip()


def key_in(home: Path) -> Path:
    (home / ".ssh").mkdir(parents=True)
    key = home / ".ssh" / "id_rsa_aabbcc"
    key.write_text("ключ\n", encoding="utf-8")
    return key


def board(tmp_path: Path, text: str = "# Прогресс: 0 из 1\n") -> Path:
    path = tmp_path / "STATUS.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_board_is_published_to_the_status_branch_and_only_there(tmp_path: Path) -> None:
    work, origin, sha = server(tmp_path)
    key = key_in(tmp_path / "home")
    rep = report(work, JUNIT_OK)
    message = state.publish(
        work,
        board(tmp_path),
        rep,
        branch="main",
        pr_number="",
        sha=sha,
        key=key,
        push_url=str(origin),
    )
    assert message.startswith("табло опубликовано на ветке status"), message
    shown = git(origin, "show", "status:state/STATUS.md")
    assert "# Прогресс: 0 из 1" in shown
    assert git(origin, "show", "status:state/test-report.commit").strip() == sha[:7]
    assert git(origin, "rev-parse", "main").strip() == sha  # main не тронута
    again = state.publish(
        work,
        board(tmp_path),
        rep,
        branch="main",
        pr_number="",
        sha=sha,
        key=key,
        push_url=str(origin),
    )
    assert again == "табло не изменилось"  # то же табло второй раз коммит не плодит


def test_without_a_write_key_the_message_names_step_v5_and_nothing_is_published(
    tmp_path: Path,
) -> None:
    """Плохой пример: ключа записи нет (В5 не сделан): понятное сообщение со ссылкой на пункт, ветки status нет, исключения нет."""
    work, origin, sha = server(tmp_path)
    assert state.write_key(tmp_path / "пустой-дом") is None
    message = state.publish(
        work,
        board(tmp_path),
        None,
        branch="main",
        pr_number="",
        sha=sha,
        key=None,
        push_url=str(origin),
    )
    assert message == "ключ записи не подключён, табло не опубликовано, шаг В5 в инструкции"
    assert "status" not in git(origin, "branch", "--list")


def test_a_rejected_push_or_unreachable_server_says_to_check_the_write_key(tmp_path: Path) -> None:
    """Плохой пример: ключ есть, но сервер недоступен или не принимает запись (ключ без Allow write access)."""
    work, _, sha = server(tmp_path)
    key = key_in(tmp_path / "home")
    message = state.publish(
        work,
        board(tmp_path),
        None,
        branch="main",
        pr_number="",
        sha=sha,
        key=key,
        push_url=str(tmp_path / "нет-сервера.git"),
    )
    assert (
        "публикация табло не удалась" in message
        and "шаг В5 в инструкции" in message
        and "Allow write access" in message
    )


def test_nothing_is_published_from_a_pull_request_a_branch_or_a_newer_main(tmp_path: Path) -> None:
    work, origin, sha = server(tmp_path)
    key = key_in(tmp_path / "home")
    for branch, pr in (("feature", ""), ("main", "7")):
        message = state.publish(
            work,
            board(tmp_path),
            None,
            branch=branch,
            pr_number=pr,
            sha=sha,
            key=key,
            push_url=str(origin),
        )
        assert message == "Не ветка main: табло собрано, но не опубликовано"
    git(work, "commit", "-q", "--allow-empty", "-m", "новее")
    git(work, "push", "-q", "origin", "main")  # на main уже более новый коммит, чем у этого прогона
    message = state.publish(
        work,
        board(tmp_path),
        None,
        branch="main",
        pr_number="",
        sha=sha,
        key=key,
        push_url=str(origin),
    )
    assert message.startswith("На main уже новее коммит")
    assert "status" not in git(origin, "branch", "--list")


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("https://github.com/pete-doc/proj.git", "pete-doc/proj"),
        ("git@github.com:pete-doc/proj.git", "pete-doc/proj"),
        ("https://x-access-token:secret@github.com/pete-doc/proj", "pete-doc/proj"),
        ("ssh://git@github.com/pete-doc/proj.git/", "pete-doc/proj"),
        ("https://gitlab.com/pete-doc/proj.git", None),
        ("https://github.com/one/two/three", None),
    ],
)
def test_the_repository_is_found_by_origin_without_printing_the_address(
    url: str, repo: str | None
) -> None:
    assert state.repo_from_origin(url) == repo


# ---------- порядок шагов и ключ записи в собранном конфиге ----------


def config(languages: list[str]) -> str:
    return str(init_module.render_circleci(languages))


def state_job(text: str) -> str:
    return text.split("\n  state:\n    docker:", 1)[1].split("\nworkflows:\n", 1)[0]


def test_the_key_comes_after_the_project_tests_and_only_in_the_state_job() -> None:
    for languages in (
        ["python"],
        ["typescript"],
        ["csharp"],
        ["powershell"],
        ["python", "typescript", "csharp", "powershell"],
    ):
        text = config(languages)
        assert text.count("- add_ssh_keys") == 1, languages  # единственный раз
        job = state_job(text)
        assert "- add_ssh_keys" in job
        order = [
            job.index(marker)
            for marker in (
                "Итог тестов",
                "name: Табло",
                "- add_ssh_keys",
                "Публикация на ветке status",
            )
        ]
        assert order == sorted(order), languages  # тесты (итог), сборка табло, ключ, публикация
        tests_at = (
            max(job.find(c) for c in ("pytest -v", "npm test", "dotnet test") if c in job)
            if any(c in job for c in ("pytest -v", "npm test", "dotnet test"))
            else -1
        )
        assert tests_at < job.index("- add_ssh_keys")  # тесты проекта идут до ключа


def standard_problems(tmp_path: Path, text: str) -> list[str]:
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8", newline="\n")
    problems: list[str] = parch_ci.circleci_problems(path, "")
    return problems


def test_standard_catches_the_key_before_the_tests_or_in_a_language_job(tmp_path: Path) -> None:
    good = config(["python"])
    assert standard_problems(tmp_path, good) == []
    # плохой пример 1: ключ записи раньше тестов проекта (код проекта выполнится с ключом)
    key_step = "      - add_ssh_keys\n"
    early = good.replace(key_step, "", 1).replace(
        "      - run:\n          name: Тесты для табло\n",
        key_step + "      - run:\n          name: Тесты для табло\n",
        1,
    )
    assert any("до тестов проекта" in line for line in standard_problems(tmp_path, early))
    # плохой пример 2: ключ в задании проверки PR (ветка PR с таким заданием получила бы право писать)
    leaked = good.replace(
        "      - checkout\n      - fetch-main\n",
        "      - checkout\n      - add_ssh_keys\n      - fetch-main\n",
        1,
    )
    assert any(
        "add_ssh_keys в задании check-python" in line
        for line in standard_problems(tmp_path, leaked)
    )


def test_the_state_job_matches_the_primary_test_language() -> None:
    py, ts, cs, ps = (config([lang]) for lang in ("python", "typescript", "csharp", "powershell"))
    assert (
        'verdict --report "test-report.xml"' in py
        and "timeout 600 pytest -v --junitxml=test-report.xml" in state_job(py)
    )
    assert 'verdict --report "test-report.json"' in ts and "timeout 600 npm test" in state_job(ts)
    assert (
        'verdict --report "test-results"' in cs
        and "dotnet test --no-restore --logger trx" in state_job(cs)
    )
    assert 'verdict --report ""' in ps and "Тесты для табло" not in state_job(
        ps
    )  # тестов нет: итог «нет тестов»
    both = config(["powershell", "csharp", "typescript"])  # первый язык с тестами: csharp
    assert 'verdict --report "test-results"' in both


def test_init_copies_the_state_script_and_a_state_workflow_that_runs_only_on_main(
    tmp_path: Path,
) -> None:
    request = {
        "project_dir": str(tmp_path), "name": "Мой сервис", "languages": ["python"],
        "description": "Считает заказы.", "priorities": "надёжность",
        "target_os": "windows",
        "goal": {"summary": "Считает.", "audience": "Продавцы.", "criteria": ["Сумма верна"], "out_of_scope": ["Оплата"]},
    }  # fmt: skip
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env=os.environ,
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    assert (tmp_path / ".github" / "parch" / "parch_state.py").read_bytes() == (
        PARCH / "parch_state.py"
    ).read_bytes().replace(b"\r\n", b"\n")
    text = (tmp_path / ".circleci" / "config.yml").read_text(encoding="utf-8")
    workflows = text.split("\nworkflows:\n", 1)[1]
    assert (
        "  state:\n    when:\n      equal: [main, << pipeline.git.branch >>]\n    jobs:\n      - state\n"
        in workflows
    )


# ---------- шаг тестов реально выполняется под bash -eo pipefail, как в CircleCI ----------


def command_of_the_tests_step(language: str) -> str:
    """Команда шага «Тесты для табло» из собранного конфига (как её запускает CircleCI)."""
    job = state_job(config([language]))
    start = job.index("name: Тесты для табло")
    begin = job.index("command: |\n", start) + len("command: |\n")
    body: list[str] = []
    for line in job[begin:].splitlines():
        if line.strip() and not line.startswith(" " * 12):
            break
        body.append(line)
    return "\n".join(line[12:] for line in body)


def run_tests_step(tmp_path: Path, command: str, tool: str, tool_code: int) -> tuple[int, str]:
    """Запуск шага в bash -eo pipefail с подставными `timeout` и инструментом тестов, возвращающим tool_code."""
    bash = __import__("shutil").which("bash")
    assert bash, "для проверки шага тестов нужен bash"
    fake = tmp_path / "bin"
    fake.mkdir(parents=True)
    for name, body in (("timeout", 'shift\nexec "$@"\n'), (tool, f"exit {tool_code}\n")):
        script = fake / name
        script.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        script.chmod(0o755)
    exit_file = tmp_path / "tests-exit"
    env = {**os.environ, "PATH": fake.as_posix() + os.pathsep + os.environ.get("PATH", "")}
    done = subprocess.run(
        [bash, "-eo", "pipefail", "-c", command.replace("/tmp/tests-exit", exit_file.as_posix())],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", env=env, timeout=60, check=False,
    )  # fmt: skip
    recorded = exit_file.read_text(encoding="utf-8").strip() if exit_file.exists() else ""
    return done.returncode, recorded


@pytest.mark.parametrize(
    ("language", "tool"), [("python", "pytest"), ("typescript", "npm"), ("csharp", "dotnet")]
)
def test_failed_tests_still_record_their_exit_code_and_the_step_goes_on(
    tmp_path: Path, language: str, tool: str
) -> None:
    """Под `bash -eo pipefail` упавшие тесты не должны обрывать шаг: иначе нет итога, табло и публикации."""
    command = command_of_the_tests_step(language)
    assert run_tests_step(tmp_path / "ok", command, tool, 0) == (0, "0")
    assert run_tests_step(tmp_path / "red", command, tool, 1) == (
        0,
        "1",
    )  # шаг зелёный, код тестов записан
    assert run_tests_step(tmp_path / "five", command, tool, 5) == (0, "5")
    # плохой пример: прежняя запись шага (без `|| code=$?`) под -e обрывается на упавших тестах и кода не записывает
    old = f"timeout 600 {tool}\necho $? > /tmp/tests-exit\nexit 0\n"
    assert run_tests_step(tmp_path / "old", old, tool, 1) == (1, "")


def test_standard_catches_any_test_step_after_the_key_not_only_the_first(tmp_path: Path) -> None:
    good = config(["python"])
    extra = "      - add_ssh_keys\n      - run:\n          name: ещё тесты\n          no_output_timeout: 5m\n          command: timeout 60 pytest -q\n"
    late = good.replace("      - add_ssh_keys\n", extra, 1)
    assert any("до тестов проекта" in line for line in standard_problems(tmp_path, late))
    assert standard_problems(tmp_path, good) == []
