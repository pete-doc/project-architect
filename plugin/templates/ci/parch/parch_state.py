"""Табло в CircleCI (блок F24, PR 2; ADR-0022): итог тестов, пометка, публикация на ветку `status`.

Команды для задания `state` (только ветка main, после слияния):

    verdict   итог тестов: упали / нет отчёта при существующих тестах / нет тестов / всё хорошо
    annotate  пометка об итоге вверху STATUS.md
    publish   публикация STATUS.md на ветку `status` по SSH-ключу записи (ключ добавляет шаг
              add_ssh_keys, он стоит после тестов проекта)
    final     цвет задания: красное, если тесты упали или отчёта нет при существующих тестах

Цвет задания задают только тесты: нет ключа записи или публикация не удалась это понятное
сообщение по-русски, а не падение. Скрипт копируется в проект как `.github/parch/parch_state.py`.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

KEY_NOT_CONNECTED = "ключ записи не подключён, табло не опубликовано, шаг В5 в инструкции"
NO_TESTS = "в проекте нет тестов"
PYTEST_NO_TESTS = 5  # код выхода pytest, когда не собрано ни одного теста
WRITE_HINT = (
    "проверьте, что ключ записи добавлен с правом записи (Allow write access), шаг В5 в инструкции"
)


@dataclass(frozen=True)
class Verdict:
    note: str
    red: bool


def tests_exist(project: Path) -> bool:
    """В проекте есть тесты: baseline перечисляет хотя бы один (его заводит init)."""
    path = project / "state" / "baseline.json"
    if not path.is_file():
        return False
    try:
        data = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return False
    tests = cast("dict[str, object]", data).get("tests") if isinstance(data, dict) else None
    if not isinstance(tests, dict):
        return False
    return any(bool(value) for value in cast("dict[str, object]", tests).values())


def failed_tests(report: Path) -> int:
    """Число упавших тестов в отчёте любого формата (JUnit, JSON, trx)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import parch_ci

    outcomes = parch_ci.any_report_outcomes(report)
    return sum(1 for state in outcomes.values() if state == "failed")


def verdict(project: Path, report: Path | None, exit_code: int) -> Verdict:
    """Итог тестов по коду выхода и отчёту (решение владельца, 2026-10-06).

    Тесты упали: красное. Отчёта нет при существующих тестах: красное (пропавший отчёт не
    должен выглядеть зелёным). Ни одного теста (код 5 pytest и пустой baseline): зелёное,
    табло так и пишет.
    """
    exist = tests_exist(project)
    if exit_code == PYTEST_NO_TESTS and not exist:
        return Verdict(NO_TESTS, False)
    if report is None or not report.exists():
        if not exist:
            return Verdict(NO_TESTS, False)
        note = "отчёта тестов нет при существующих тестах: тесты не запустились"
        return Verdict(note, True)
    try:
        failed = failed_tests(report)
    except Exception as error:  # нечитаемый отчёт это красное, а не падение скрипта
        return Verdict(f"отчёт тестов не читается ({error}): тесты не запустились", True)
    if failed:
        return Verdict(f"тесты упали: {failed}", True)
    if exit_code != 0:
        return Verdict(f"тесты завершились с кодом {exit_code}", True)
    return Verdict("", False)


def annotate(status_text: str, note: str) -> str:
    """Пометка вверху табло, сразу после заголовка."""
    if not note:
        return status_text
    lines = status_text.splitlines()
    return "\n".join([*lines[:1], "", f"> **Тесты:** {note}", *lines[1:]]) + "\n"


def repo_from_origin(url: str) -> str | None:
    """`владелец/репозиторий` по адресу origin (в нём может быть токен: наружу идёт только имя)."""
    rest = re.sub(r"^[A-Za-z+]+://([^/@]*@)?", "", url.strip())
    rest = re.sub(r"^git@", "", rest)
    if not re.match(r"^github\.com[:/]", rest):
        return None
    repo = re.sub(r"^github\.com(:[0-9]+)?[:/]", "", rest).strip("/")
    repo = re.sub(r"\.git$", "", repo)
    return repo if re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo) else None


def write_key(home: Path) -> Path | None:
    """Ключ записи от `add_ssh_keys` (`~/.ssh/id_rsa_<отпечаток>`); без него публикации нет."""
    folder = home / ".ssh"
    keys = sorted(folder.glob("id_rsa_*")) if folder.is_dir() else []
    return keys[0] if keys else None


def git(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    identity = ["-c", "user.name=circleci-state"]
    identity += ["-c", "user.email=circleci-state@users.noreply.github.com"]
    return subprocess.run(
        ["git", *identity, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
        env={**os.environ, **(env or {})},
    )


def last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def publish(
    project: Path,
    status_md: Path,
    report: Path | None,
    *,
    branch: str,
    pr_number: str,
    sha: str,
    key: Path | None,
    push_url: str | None = None,
) -> str:
    """Публикует табло на ветку `status`; возвращает сообщение. Не падает, если публикации нет."""
    short = sha[:7]
    if branch != "main" or pr_number:
        return "Не ветка main: табло собрано, но не опубликовано"
    if key is None:
        return KEY_NOT_CONNECTED
    if push_url is None:
        origin = git(project, "remote", "get-url", "origin")
        repo = repo_from_origin(origin.stdout) if origin.returncode == 0 else None
        if repo is None:
            return "не удалось определить репозиторий GitHub по origin: табло не опубликовано"
        push_url = f"git@github.com:{repo}.git"
    command = f'ssh -i "{key.as_posix()}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
    ssh = {"GIT_SSH_COMMAND": command}
    failure = f"публикация табло не удалась: {WRITE_HINT}"
    latest = git(project, "ls-remote", push_url, "refs/heads/main", env=ssh)
    if latest.returncode != 0:
        return f"{failure}; не удалось прочитать main: {last_line(latest.stderr)}"
    head = latest.stdout.split("\t", 1)[0].strip()
    if head and head != sha:
        return (
            f"На main уже новее коммит: табло для {short} не публикуется (опубликует новый прогон)"
        )
    work = Path(tempfile.mkdtemp(prefix="parch-status-"))
    try:
        git(work, "init", "-q", "-b", "status")
        git(work, "remote", "add", "origin", push_url)
        if git(work, "fetch", "-q", "--no-tags", "origin", "status", env=ssh).returncode == 0:
            git(work, "checkout", "-q", "-B", "status", "FETCH_HEAD")
        (work / "state").mkdir(exist_ok=True)
        shutil.copyfile(status_md, work / "state" / "STATUS.md")
        if report is not None and report.is_file():
            shutil.copyfile(report, work / "state" / f"test-report{report.suffix}")
            (work / "state" / "test-report.commit").write_text(short + "\n", encoding="utf-8")
        git(work, "add", "state")
        if git(work, "diff", "--cached", "--quiet").returncode == 0:
            return "табло не изменилось"
        git(work, "commit", "-q", "-m", f"state: табло на коммите {short}")
        pushed = git(work, "push", "-q", "origin", "HEAD:status", env=ssh)
        if pushed.returncode != 0:
            return f"{failure}: {last_line(pushed.stderr)}"
        return f"табло опубликовано на ветке status (коммит {short})"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def read_verdict(path: Path) -> Verdict:
    data = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    return Verdict(str(data.get("note", "")), bool(data.get("red", False)))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="parch_state")
    parser.add_argument("command", choices=["verdict", "annotate", "publish", "final"])
    parser.add_argument("--project", default=".")
    parser.add_argument("--report", default="")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--verdict", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--status", default="")
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    report = Path(args.report) if args.report else None
    if args.command == "verdict":
        result = verdict(project, report, args.exit_code)
        payload = json.dumps({"note": result.note, "red": result.red}, ensure_ascii=False)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[parch:state] итог тестов: {result.note or 'всё хорошо'}")
        return 0
    if args.command == "annotate":
        path = Path(args.file)
        text = annotate(path.read_text(encoding="utf-8"), read_verdict(Path(args.verdict)).note)
        path.write_text(text, encoding="utf-8", newline="\n")
        return 0
    if args.command == "publish":
        message = publish(
            project,
            Path(args.status),
            report,
            branch=os.environ.get("CIRCLE_BRANCH", ""),
            pr_number=os.environ.get("CIRCLE_PR_NUMBER", ""),
            sha=os.environ.get("CIRCLE_SHA1", ""),
            key=write_key(Path.home()),
            push_url=os.environ.get("PARCH_PUSH_URL") or None,
        )
        print(f"[parch:state] {message}")
        return 0
    result = read_verdict(Path(args.verdict))
    print(f"[parch:state] {'ПРОВАЛ: ' + result.note if result.red else 'ok'}")
    return 1 if result.red else 0


if __name__ == "__main__":
    sys.exit(main())
