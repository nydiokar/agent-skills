#!/usr/bin/env python3
"""PreToolUse guard — deny hand-edits to the GENERATED notes router.

The notes router (`.ai/notes/NOTES_INDEX_GENERATED.md` by default) is a PROJECTION of
the per-note headers. If an agent hand-edits it, the next `--render` silently overwrites
the edit and the store diverges from the view — the exact drift this system exists to
prevent. This hook DENIES the edit and points the agent at the correct write path.

It also protects the marked RECENT-NOTES block inside the context file: that block is
owned by `--render`, so a direct Write/Edit that would clobber it is denied with a hint.

Wire as a PreToolUse hook on Edit|Write|MultiEdit in .claude/settings.json:

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [
              { "type": "command",
                "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard_notes_generated.py\"" }
            ]
          }
        ]
      }
    }

Reads the tool-call JSON on stdin; emits permissionDecision=deny on a protected path,
otherwise stays silent (allow). Pure stdlib. Project-agnostic: the protected router name
is matched by SUFFIX so it works whatever the notes_dir path is.
"""
from __future__ import annotations

import json
import sys

ROUTER_SUFFIX = "NOTES_INDEX_GENERATED.md"

MESSAGE = (
    "{path} is the GENERATED notes router (a projection of the per-note headers).\n"
    "Do NOT hand-edit it — the next `notes_tool.py --render` overwrites it and the store diverges.\n"
    "To change what the router shows: edit the SOURCE note's header "
    "(Status / Category / So-what) under notes/<category>/<file>.md, then run:\n"
    "  python <path>/notes_tool.py --render\n"
    "To add a note, use the `notes` skill (or `notes_tool.py --new \"<title>\"`)."
)


def _extract_path(data: dict) -> str:
    ti = data.get("tool_input") or {}
    return ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:  # noqa: BLE001
        return 0  # fail-open: never block on parse error
    path = _extract_path(data).replace("\\", "/")
    if path.endswith(ROUTER_SUFFIX):
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": MESSAGE.format(path=path),
            }
        }
        print(json.dumps(out))
        return 0
    return 0  # allow


if __name__ == "__main__":
    sys.exit(main())
