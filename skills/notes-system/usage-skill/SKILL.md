---
name: notes
description: Write, audit, and archive durable shift-log notes in `.ai/notes/`. Use at the end of a session to record the handoff (what happened / what's hot / next task / watch out / done) as a categorized per-note file INSTEAD of dumping into the context file, and use to run the keep/archive audit that flips settled notes into `_archive/`. Triggers on: write my shift note, record the handoff, end-of-shift note, add a note to .ai/notes, audit the notes, archive settled notes, prune the shift log. NOT for a task's final verdict or a measured result with a CI (those go to your project's results/decision store).
---

# The `.ai/notes/` shift-log skill

You own the **durable shift log**. An agent finishing a session leaves a handoff for the next
agent. It goes in `.ai/notes/<category>/YYYY-MM-DD-<slug>.md` — **never** dumped into the context
file. Format and rules: `.ai/notes/README.md` and `.ai/notes/AGENTS.md`. The categories + routing
keywords for THIS project live in `.ai/notes/notes.config.json` — read them before categorizing.

Find the tool: it is `notes_tool.py` (wherever this project installed it — commonly
`scripts/notes/notes_tool.py` or a package script like `notes:render` / `notes:selftest`). If you
can't locate it, `ls` for `notes_tool.py`.

## The model you enforce
- **One note per handoff**, filed by durable category. Pick by FIRST keyword match in
  `notes.config.json`; the last category is the default fallthrough.
- **A note is a cross-arc HANDOFF pointer** — NOT a task's final verdict, NOT a measured result. A
  number with a CI NEVER lives in a note body; it gets a POINTER to wherever results live.
- **Keep/archive is SEMANTIC, not by clock.** `live` while re-reading it would change the next
  agent's move; `settled` when the arc is closed and its durable content lives elsewhere. No cap, no FIFO.

## WRITE a note (end of shift)
1. **Supersession check.** `grep -ril "<JOB_ID or arc>" .ai/notes/` — if a prior live note covers
   the same arc, set `Supersedes:` on the new one and flip the prior to `Status: settled`.
2. **Pick the category** by first keyword match in `notes.config.json`.
3. **Create** the file — easiest via the tool (fills the header + skeleton):
   `python <path>/notes_tool.py --new "<title>" --category <cat>` then fill the body. Or author it
   directly with this exact header + the 5-part body:
   ```
   # NOTE: <title>

   Status: live
   Category: <cat>
   Date: <YYYY-MM-DD>
   Job: <id>                # optional cross-link
   Supersedes: <file>       # optional
   So-what: <ONE line — the router shows exactly this>

   **What just happened.** ...
   **What's hot.** ...
   **Exact next task.** ...
   **Watch out for.** ...
   **Done and closed.** ...
   ```
4. **REFUSE to paste a CI/number into the body.** If the note needs a result, cite its id and point
   at the results store. (Regex smell to reject: `\[\s*-?\d*\.?\d+\s*,\s*-?\d*\.?\d+\s*\]`.)
5. **Render:** `python <path>/notes_tool.py --render` — regenerates the router AND refreshes the
   `<!-- RECENT NOTES -->` block in the context file. Do NOT hand-edit either (hook-denied).

## AUDIT + ARCHIVE (keep/archive pass)
1. List live notes: the LIVE table in `NOTES_INDEX_GENERATED.md`.
2. For each, apply the test: would re-reading it change what the next agent does? If NO and the arc
   is closed (its durable home exists) → it is `settled`.
3. To settle: move the file to `.ai/notes/_archive/`, **verbatim**. Before removing the source,
   VERIFY on disk (`wc -l` + `grep` the destination). Never summarize, never delete — evict.
   (Simplest: `git mv .ai/notes/<cat>/<f>.md .ai/notes/_archive/<f>.md`, then flip `Status:` to
   `settled` in the moved file.)
4. `--render` + `--selftest`.

## VERIFY (always, before you stop)
```
python <path>/notes_tool.py --selftest    # headers + no-CI-in-body + router determinism, must be GREEN
```

## Guardrails
- Unknown category → STOP, ask the owner (do not invent a new folder; editing the taxonomy is an
  owner decision, like an ADR).
- If your handoff is really a task's final conclusion → it belongs in that task's verdict/record,
  not here. A note POINTS at it.
- If it's a durable world-fact or a binding choice → your project's knowledge/decision store. A
  note POINTS at those.
