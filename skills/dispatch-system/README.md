# dispatch-system — portable, date-organized job-state tracker

A drop-in **job registry** where the folder IS the source of truth. Each job is a markdown file
with a small ` ```yaml ` state block at its top; the tool scans them into generated views, detects
gaps structurally, and keeps everything honest. Jobs are organized into **date-buckets**
(`<dispatch>/YYYY-MM/<JOB>.md`) so the registry stays navigable at hundreds of jobs instead of
collapsing into one flat dump.

Same store→projection→guard shape as the other registry skills:
- **store** — `<dispatch>/<YYYY-MM>/<JOB_ID>.md`, each with a yaml state block (the only edit surface).
- **projection** — `dispatch_state.py --render` builds `_DISPATCH_STATE.md` (eyeball) +
  `_dispatch.parquet` (query).
- **guard** — a warn-only git pre-commit hook re-renders the views and flags gaps on every commit.
- **organization** — `--organize` buckets jobs by canonical `created_at`; `--new` files fresh jobs
  into today's bucket automatically.

## Why date-organized (the improvement)

A flat `.ai/dispatch/` folder works until it doesn't — at a few hundred `*.md` files it's
unnavigable and every `ls` is noise. This version groups jobs into `YYYY-MM/` folders by their
canonical `created_at`. Discovery is **recursive**, so every command (`audit`/`set`/`next`/`render`)
works across buckets unchanged, and a job is still addressed by its bare `JOB_ID`. `created_at`
never changes, so a job never hops buckets once filed. Granularity is configurable:
`DISPATCH_BUCKET=year|month|day` (default `month`).

## What's in here

```
dispatch-system/
  SKILL.md                     installer skill (scaffold into a target project)
  usage-skill/SKILL.md         the `dispatch` usage skill (create/set/audit/next/organize)
  tool/dispatch_state.py       the scanner + projection engine + organizer (needs pyyaml/pandas/pyarrow)
  templates/CLAUDE.md          the per-job protocol doc dropped into the target's dispatch dir
  templates/.dispatch_not_a_job  sidecar example (filenames that aren't jobs)
```

## Commands

Replace `<cmd>` with your invocation (default `python scripts/dispatch/dispatch_state.py --`; set
`DISPATCH_CMD` to a wrapper like `pnpm dispatch:` so fix-hints print it).

```
<cmd>new JOB_ID       create a job stub (canonical created_at) in today's YYYY-MM bucket
<cmd>set JOB_ID F V   safely edit one yaml field (the ONLY sanctioned state edit)
<cmd>audit            the honest board across all buckets + gaps with fix-hints  (default)
<cmd>next             ranked pickable jobs: ready/active, non-recurring, P0→P3 then category then stale
<cmd>organize         move jobs into their created_at date-bucket (git-mv aware, idempotent)
<cmd>migrate          one-time: inject yaml blocks + seed created_at from first git-commit date
<cmd>render           (re)write the two generated views
<cmd>install-git-hook add the warn-only pre-commit trigger (any git backend)
<cmd>selftest         the tool's own teeth — round-trip, gap detection, created_at immutability
```

## Install

Use the `dispatch-system` installer skill (invoke it in the target and let it scaffold), or do it
by hand following `SKILL.md`. Requires Python with `pyyaml`, `pandas`, `pyarrow`.

## The gaps it detects (structurally, not by nagging)

- `MISSING_STATE` — a job file with no yaml block.
- `CLAIMED_DONE_NO_PROOF` — `status: done` but an `evidence:` path doesn't exist on disk.
- `DONE_NO_EVIDENCE` — `done` with no evidence paths at all (scoped to post-convention jobs).
- `VERDICT_NO_STATUS` — a `## Verdict` is written but status is non-terminal.
- `STALE_BLOCKED` — `blocked` but all `depends_on` are satisfied.
- `BLOCKED_NO_GATE` — `blocked` with an empty `depends_on` (an unrecorded gate isn't a satisfied one).
- `STALE_<n>d` — an `active` job untouched for >14 days.
- `BAD_STATUS` / `BAD_CATEGORY` / `BAD_PRIORITY` / `BAD_CADENCE` — value outside the closed vocabulary.
