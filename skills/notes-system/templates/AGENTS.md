# Rules for `.ai/notes/` (read before you write or read a note)

## Writing (at end of shift)

- **Do NOT dump into the context file.** A shift note's BODY goes in `.ai/notes/<category>/`, one
  file per handoff. The context file gets only the pointer (the router refreshes it automatically
  on `--render`).
- Use the **`notes` skill** to write — it enforces the header + 5-part body + one-line `So-what`,
  and refuses a CI/number in the body (that belongs with your durable results, not here).
- **Supersession check first:** before writing, grep `.ai/notes/**` for a prior note on the same
  Job/arc. If one exists, set `Supersedes:` and flip the prior note `Status: settled` in the same
  move.
- Pick the category by **first match** against the keyword rules in `notes.config.json`; the last
  category is the default fallthrough.

## Reading (at start of shift)

- The context file first — it is the board + pointers, deliberately short.
- Then the **router** `NOTES_INDEX_GENERATED.md` — skim the LIVE table, pull only the notes whose
  `So-what` is relevant to your task. **Do not read all notes.** That is the whole point — load the
  context the job needs, not the entire history.
- `_archive/` notes are **frozen historical snapshots** — greppable for background, never current
  authority. Do not edit them.

## Keep / archive

- A note stays `live` while it still steers work; it becomes `settled` when its arc is closed and
  the durable content lives elsewhere (a decision record, a spec, a results store).
- The `notes` skill runs the audit and moves settled notes to `_archive/` verbatim, verified on
  disk. Never delete, never summarize — evict.

## Non-negotiables

- **No CI/number in a note body** — pointer to the durable result only.
- **A note ≠ a verdict ≠ a durable result.** Note = cross-arc handoff.
- **Never hand-edit `NOTES_INDEX_GENERATED.md`** — it is regenerated; hand-edits are hook-denied.
