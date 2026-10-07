# notes-system — a portable shift-log for agent-driven projects

A drop-in **shift log**: the handoff one agent/session leaves for the next. One note per handoff,
filed by durable category, with a GENERATED router index and a **semantic** (not clock-based)
keep/archive test.

It exists to kill one specific anti-pattern: dumping every session's prose handoff into a single
always-read context file, then evicting the oldest by age. That bloats the file with history the
next agent doesn't need and drops good notes by *clock* instead of *relevance*. Here, the context
file stays a thin pointer; the notes live in categorized files; the router lets an agent pull only
what its task needs.

Same shape as any robust generated-index system: **store → projection → guard**.
- **store** — plain `.md` notes under `<notes_dir>/<category>/`, each with a small header.
- **projection** — `notes_tool.py --render` builds `NOTES_INDEX_GENERATED.md` (the router) and
  refreshes a marked block in your context file.
- **guard** — a PreToolUse hook denies hand-edits to the generated router so it can't drift.
- **keep/archive** — SEMANTIC: `live` while it steers the next agent, else `settled` → `_archive/`
  verbatim. No cap, no FIFO, no age trigger.

## What's in here

```
notes-system/
  tool/notes_tool.py              the projection engine + scaffolder (stdlib only, Win/POSIX)
  hook/guard_notes_generated.py   PreToolUse guard — denies edits to the generated router
  templates/                      README / AGENTS / NOTE_TEMPLATE copied into a target on --init
  skills/notes/                   the USAGE skill — how an agent writes/audits/archives a note
  skills/scaffold-notes/          the INSTALLER skill — how to port this into another project
```

## Two ways to use it

**A. Give an agent the installer skill (recommended).** Drop `skills/scaffold-notes/` into a
project's `.claude/skills/`, keep this bundle reachable, and tell the agent "scaffold the notes
system here." The skill copies the tool + hook, scaffolds the store, wires the commands and the
guard hook, tunes the taxonomy, and verifies.

**B. Manual install.**
```
# from the target project root
mkdir -p scripts/notes .claude/hooks .claude/skills
cp <bundle>/tool/notes_tool.py           scripts/notes/notes_tool.py
cp <bundle>/hook/guard_notes_generated.py .claude/hooks/guard_notes_generated.py
cp -r <bundle>/skills/notes              .claude/skills/notes
python scripts/notes/notes_tool.py --init
# then wire the PreToolUse hook (see skills/scaffold-notes/SKILL.md step 7)
# and add the <!-- RECENT NOTES --> markers to your context file
python scripts/notes/notes_tool.py --selftest
```

## Configuration

Everything project-specific lives in `<notes_dir>/notes.config.json` (written by `--init`):
- `notes_dir` — where notes live (default `.ai/notes`).
- `context_file` — the always-read file whose `<!-- RECENT NOTES -->` block gets refreshed.
- `recent_notes_count` — how many live notes to list in that block.
- `categories` — ordered list of `{name, blurb, keywords}`. **First keyword match wins**; the
  **last** category is the default fallthrough. Keep the set small on purpose.

The tool itself carries **no** project assumptions — no pnpm, no virtualenv, no third-party deps.
It discovers the notes root by walking up from the current directory, or honors `$NOTES_ROOT`.

## Commands

```
python notes_tool.py --init                         scaffold folders + config + docs (idempotent)
python notes_tool.py --new "<title>" [--category c] create a new note from the template
python notes_tool.py --render                       rebuild the router + context recent-notes block
python notes_tool.py --selftest                     headers + no-CI-in-body + router determinism
```

## The one rule that makes it work

**A note is a handoff pointer, not a results store.** A measured number with a CI never lives in a
note body — it lives in your project's durable results/decision store, and the note points at it.
Keep/archive by relevance, never by clock. That discipline is what keeps the next agent's boot
context small and true.
