"""PreToolUse и PostToolUse: журнал всех вызовов инструментов в `.claude/audit/`.

Решения охранных hooks дописывают свои строки в тот же журнал (см. _common.audit).
Журнал — по одному файлу JSONL в день, секреты в командах затираются. Никогда не блокирует.
"""

from __future__ import annotations

import sys

from _common import audit, describe_call, get_str, project_dir, read_input

HOOK = "audit_log"


def main() -> None:
    try:
        data = read_input()
        project = project_dir(data)
        event = get_str(data, "hook_event_name")
        decision = "attempt" if event == "PreToolUse" else "done"
        audit(project, data, HOOK, decision, describe_call(data))
    except Exception as error:
        sys.stderr.write(f"[{HOOK}] журнал не записан: {type(error).__name__}: {error}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
