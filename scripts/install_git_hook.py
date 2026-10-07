#!/usr/bin/env python3
"""install_git_hook.py — wire a pre-commit hook that keeps SKILLS_INDEX.md honest.

The hook regenerates the index and FAILS the commit if it changed (i.e. a skill was
added/edited without re-rendering). Idempotent; preserves any existing pre-commit hook
body. Run once after cloning:  python scripts/install_git_hook.py
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".git" / "hooks" / "pre-commit"

MARK_START = "# >>> agent-skills index guard >>>"
MARK_END = "# <<< agent-skills index guard <<<"

BLOCK = f"""{MARK_START}
# Regenerate the skills index and block the commit if it is stale or a skill is invalid.
python "$(git rev-parse --show-toplevel)/scripts/index_skills.py" --selftest || {{
  echo "[pre-commit] skills selftest FAILED — fix the skill or run: python scripts/index_skills.py --render"
  exit 1
}}
if ! git diff --quiet -- SKILLS_INDEX.md; then
  echo "[pre-commit] SKILLS_INDEX.md was regenerated — staging it. Re-run the commit."
  git add SKILLS_INDEX.md
  exit 1
fi
{MARK_END}
"""


def main() -> int:
    if not (ROOT / ".git").is_dir():
        print("[error] not a git repo (no .git)", file=sys.stderr)
        return 2
    HOOK.parent.mkdir(parents=True, exist_ok=True)
    existing = HOOK.read_text(encoding="utf-8") if HOOK.exists() else ""
    if MARK_START in existing:
        # replace the block between markers
        pre = existing.split(MARK_START)[0].rstrip("\n")
        post = existing.split(MARK_END)[-1].lstrip("\n")
        body = (pre + "\n\n" if pre else "") + BLOCK + ("\n" + post if post else "")
    elif existing.strip():
        body = existing.rstrip("\n") + "\n\n" + BLOCK
    else:
        body = "#!/bin/sh\n\n" + BLOCK
    HOOK.write_text(body, encoding="utf-8")
    HOOK.chmod(HOOK.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[install] wrote pre-commit hook -> {HOOK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
