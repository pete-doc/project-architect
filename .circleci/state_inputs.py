"""Данные для табло: открытые PR репозитория (через API GitHub, как `gh pr list --json ...`).

Запуск: python3 .circleci/state_inputs.py <файл.json>. Репозиторий публичный, список PR
читается без токена; при ошибке пишется пустой список: табло тогда без раздела «открытые PR»,
сборка не падает.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def open_pull_requests(owner: str, repo: str) -> list[dict[str, object]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=100"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - фиксированный адрес GitHub
        raw = json.load(response)
    return [
        {
            "number": item["number"],
            "title": item["title"],
            "isDraft": bool(item.get("draft")),
            "labels": [{"name": label["name"]} for label in item.get("labels", [])],
            "statusCheckRollup": [],
        }
        for item in raw
    ]


def main() -> int:
    out = sys.argv[1]
    owner = os.environ.get("CIRCLE_PROJECT_USERNAME", "")
    repo = os.environ.get("CIRCLE_PROJECT_REPONAME", "")
    try:
        prs = open_pull_requests(owner, repo)
    except (OSError, ValueError, KeyError) as error:
        sys.stderr.write(f"Открытые PR получить не удалось: {error}\n")
        prs = []
    with open(out, "w", encoding="utf-8") as file:
        json.dump(prs, file, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
