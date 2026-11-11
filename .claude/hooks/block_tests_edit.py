# STRETCH GOAL 1
"""PreToolUse hook: stop Claude Code from editing files in the tests folder.

Claude Code sends the tool call as JSON on stdin. If the file it wants to change is
inside tests/, this script prints a message to stderr and exits with code 2, which
blocks the call and tells Claude why. Any other file is allowed (exit code 0).
"""

import json
import os
import sys
from pathlib import Path


def target_path(tool_input):
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def main():
    try:
        call = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # nothing we can check, so do not block

    path = target_path(call.get("tool_input") or {})
    if not path:
        return 0

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or call.get("cwd") or os.getcwd()).resolve()
    target = Path(path)
    target = (target if target.is_absolute() else project / target).resolve()

    if target == project / "tests" or (project / "tests") in target.parents:
        print(
            f"Blocked: {path} is in the tests folder. The tests are the spec, so do not edit "
            "them. If a test looks wrong, stop and ask the user.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
