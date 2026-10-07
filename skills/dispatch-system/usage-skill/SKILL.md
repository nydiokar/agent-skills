---
name: dispatch
description: Track and work dispatched jobs in a date-organized `.ai/dispatch/` registry where each job's state is a yaml block in its own file. Use to create a job, change a job's status/labels, see the honest board, pick what to work next, resolve audit gaps, or tidy the folder into date-buckets. Triggers on: create a dispatch job, set a job status, mark a job done, show the dispatch board, what should I work on next, audit the dispatch queue, organize the dispatch folder. NOT for writing a job's verdict prose (that goes in the job's own ## Verdict section).
---

# The dispatch job-state skill

You operate a **folder-is-the-registry** job tracker. Each job is `<dispatch>/<YYYY-MM>/<JOB_ID>.md`
with a ` ```yaml ` state block at its top. The folder (recursively, across date-buckets) IS the
registry; the generated views are projections. Full protocol: the dispatch `CLAUDE.md` in the
dispatch dir. Find the tool: `dispatch_state.py` (commonly `scripts/dispatch/dispatch_state.py`, or
a package script). Below, `<cmd>` = the project's invocation (default
`python scripts/dispatch/dispatch_state.py --`; a wrapper is exposed via `DISPATCH_CMD`).

## The model you enforce
- **One job = one file with one yaml state block.** That block is the ONLY state edit surface.
- **`created_at` is canonical** — stamped once, never changed; it picks the job's date-bucket.
- **`status: done` needs `evidence:`** — artifact paths that are checked to exist on disk.
- **State edits go through the tool, never a hand-regex** — `set` round-trips through yaml and
  reverts if the edit would corrupt the block.

## CREATE a job
```
<cmd>new JOB_ID
```
Stamps `created_at` now, files the stub into today's `YYYY-MM/` bucket, writes the brief skeleton.
Then fill the brief (Goal / Task / Done-when). Do NOT hand-write a yaml block.

## CHANGE state or labels
```
<cmd>set JOB_ID status active        # ready | active | blocked | done | dead
<cmd>set JOB_ID evidence "a.parquet,b.json"   # proof paths for a done job
<cmd>set JOB_ID depends_on "OTHER_JOB"        # gate
<cmd>set JOB_ID category research             # research|fix|build|ops|charter
<cmd>set JOB_ID priority P1                    # P0|P1|P2|P3
```
Address a job by its bare `JOB_ID` — the tool finds it in whatever bucket it lives in.
`updated_at` bumps automatically.

## SEE the board / PICK work
```
<cmd>audit     # the honest queue across all buckets + any gaps with fix-hints
<cmd>next      # ranked pickable jobs (ready/active, non-recurring), P0→P3 then category then stale
```
Pick from `next`, not by scanning the folder.

## RESOLVE a gap the audit shows
Each gap prints its own fix command. Common ones:
- `MISSING_STATE` → `<cmd>set J status <s>` (or `new` if brand new).
- `CLAIMED_DONE_NO_PROOF` → fix the `evidence:` path, or set status back.
- `VERDICT_NO_STATUS` → a `## Verdict` is written but status isn't terminal → `set J status done`.
- `STALE_<n>d` → an `active` job untouched >14d → finish / block / kill it.

## TIDY the folder (occasionally, or after a bulk import)
```
<cmd>organize
```
Moves each job file into the `YYYY-MM/` bucket its `created_at` implies (git-mv aware, idempotent).
Set `DISPATCH_BUCKET=year|month|day` to change granularity (default `month`).

## VERIFY
```
<cmd>selftest    # the tool's own teeth — round-trip, gap detection, created_at immutability
```

## Guardrails
- A job's VERDICT prose goes in its own `## Verdict` section, with `results_ref:` pointing at it —
  NOT in a shared log and NOT in the yaml block.
- Never hand-edit a generated view or a yaml block. Never change `created_at`.
- If a file in the dispatch tree is not a job (a doc, an index), add its filename to
  `.dispatch_not_a_job` so it stops showing as `MISSING_STATE`.
