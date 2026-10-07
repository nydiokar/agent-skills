# `.ai/notes/` — the durable shift log (per-note, categorized)

**This is the shift log.** One note per handoff, filed by durable category, with a generated
router and a *semantic* keep/archive test. It replaces the anti-pattern where every session
dumps a dated prose blob into one always-read context file and a clock-based rule evicts the
oldest — which grows that file to tens of thousands of tokens of history the next agent mostly
doesn't need, and evicts good notes by age instead of relevance.

Same store→projection→guard pattern you'd use for any generated index: the notes are the store,
the router is a projection, a hook guards the projection, and keep/archive is by *relevance*.

## Why this exists

The context file is read at the start of **every** session. Load the context the agent needs for
the job — not all prior history. The context file is a thin pointer; the notes live here,
categorized, and the router lets an agent pull only the ones relevant to its task.

## Layout

```
.ai/notes/
  <category>/   one folder per durable category (see notes.config.json)
  _archive/     settled notes that no longer steer work — frozen, greppable, off the boot path
  NOTES_INDEX_GENERATED.md   the router (GENERATED — hook-denied from hand edits)
  notes.config.json          categories + routing keywords + context-file path
  NOTE_TEMPLATE.md           the header + 5-part body skeleton
```

Categories are deliberately few (friction by design — adding one is an owner decision). If a note
fits none, that is the STOP signal: ask the owner. The **last** category in the config is the
default fallthrough.

## Filename

`YYYY-MM-DD-<slug>.md` — the date is when the note was written (the handoff); the slug is the
subject. **The filename tells you what it is before you open it.**

## Note format

```
# NOTE: <title>

Status: live            # live = still steers the next agent | settled = kept for history only
Category: <one of the configured categories>
Date: YYYY-MM-DD
Job: <id>               # optional — the task/ticket it came from (cross-link, NOT a copy)
Supersedes: <file>      # optional — prior note on the same arc
So-what: <ONE line — the router shows exactly this>

**What just happened.** ...
**What's hot.** ...
**Exact next task.** ...
**Watch out for.** ...
**Done and closed.** ...
```

The 5-part body is the skeleton — keep it. **A measured result with a number/CI does NOT live in a
note body** — it lives wherever your project keeps durable results; the note carries a POINTER.
A note is a cross-arc HANDOFF, not a task's final verdict.

## The keep / archive test (semantic, NOT by clock)

- A note is **`live`** while re-reading it would **change what the next agent does**.
- It flips to **`settled`** when the arc is closed and its durable content lives elsewhere.
  Re-reading it wouldn't alter a move.
- Settled notes move to `_archive/` **verbatim** (never summarized), verified on disk before the
  source is removed. **No cap. No FIFO. No age trigger.** Do not archive toward a quota.

## Commands

```
python <path>/notes_tool.py --render     # regenerate the router + refresh the context recent-notes block
python <path>/notes_tool.py --selftest   # header well-formedness + no-CI-in-body + router determinism
python <path>/notes_tool.py --new "<title>" [--category <cat>]   # scaffold a new note
```

To WRITE a note or run the keep/archive audit, use the **`notes` skill** (it enforces the header,
the no-CI-in-body rule, the supersession check, and the on-disk-verified archive move).
