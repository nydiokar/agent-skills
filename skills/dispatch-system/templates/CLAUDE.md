# Dispatch protocol — how job state works here (read before touching a dispatch file)

Job STATE is machine-tracked. You do **one thing**: keep the `status:` in a job's OWN `.md` file
correct. Everything else (rendering the board, gap/proof checks, date-bucketing) is automatic.

## The one rule
Each job lives at `<dispatch>/<YYYY-MM>/<JOB_ID>.md` and has a ` ```yaml ` block at its top = its
state. **That block is the source of truth.** The folder is the registry — a file with no block is
a detected gap. You address a job by its bare `JOB_ID`; the tool finds it in whatever date-bucket
it sits in.

## Commands (the whole workflow)

Replace `<cmd>` with your project's invocation. By default that's
`python scripts/dispatch/dispatch_state.py --`; if you wired a wrapper (pnpm/npm/make), set
`DISPATCH_CMD` to it so the fix-hints print your wrapper.

| When | Run |
|---|---|
| **Start a new job** | `<cmd>new JOB_ID` — stamps a canonical `created_at`, writes the brief stub INTO today's date-bucket. Then fill the brief. |
| **Change a job's state or labels** | `<cmd>set JOB_ID status done` — statuses: `ready` \| `active` \| `blocked` \| `done` \| `dead`. Also `set ID owner <name>` / `depends_on` / `results_ref` / `category` / `priority` / `cadence`. |
| **See the honest board** | `<cmd>audit` — the queue + any gaps, across all date-buckets. |
| **Pick what to work next** | `<cmd>next` — ranked ready/active non-recurring jobs, P0→P3 then category then staleness. Blocked/done/dead/recurring excluded. |
| **Tidy the folder** | `<cmd>organize` — move job files into the date-bucket their `created_at` implies (git-mv aware, idempotent). Run after a bulk import or occasionally. |

**Three optional taxonomy fields** on every job (set via `set`, never hand-edited):
- `category: research \| fix \| build \| ops \| charter` — kind of work; drives grouping in `next`
- `priority: P0 \| P1 \| P2 \| P3` — urgency within-category (P0=blocking now, P3=parked)
- `cadence: once \| recurring` — `recurring` jobs are excluded from `next`

Empty/absent = valid (old jobs without these fields stay clean; `priority` defaults to P2 in `next`).

## Date-organized layout (the improvement over a flat dump)
Jobs are grouped into `<dispatch>/YYYY-MM/` folders by their canonical `created_at` — so a few
hundred jobs stay navigable instead of one giant folder. Granularity is configurable with
`DISPATCH_BUCKET=year|month|day` (default `month`). Discovery is recursive: every command works
across buckets unchanged. `created_at` (which picks the bucket) is NEVER changed, so a job never
hops buckets once filed; `organize` only relocates files that are in the wrong place.

You do **not** run `render` by hand — the **git pre-commit hook** re-renders `_DISPATCH_STATE.md`
+ `_dispatch.parquet` on every commit and warns (never blocks) if state is dirty, auto-staging the
fresh views so they never drift. One-time setup per repo:
`python scripts/dispatch/dispatch_state.py --install-git-hook`.

## Non-negotiables
- **NEVER hand-edit the yaml block with a regex/sed** — it corrupts them. Use `set`. It round-trips
  through yaml and verifies the block still parses (and reverts the write if it wouldn't).
- **NEVER hand-edit `_DISPATCH_STATE.md` or `_dispatch.parquet`** — generated.
- **`created_at` is CANONICAL** — written once at dispatch, never changed, never re-derived. Only
  `updated_at` bumps (automatically, on `set`).
- **`status: done` needs proof.** Set `evidence:` to artifact paths that prove the job ran. The
  audit flags `CLAIMED_DONE_NO_PROOF` if a `done` job's evidence path is missing on disk.

## What each file is (don't conflate them)
- `<YYYY-MM>/<JOB>.md` — the BRIEF (what to do) + its yaml state block + the job's `## Verdict`
  section. Your edit surface. Point `results_ref:` at the verdict heading.
- `_DISPATCH_STATE.md` / `_dispatch.parquet` — GENERATED views. Read, never write.
- `.dispatch_not_a_job` — optional sidecar listing filenames in the dispatch tree that are NOT jobs
  (one per line), so non-job docs are not flagged `MISSING_STATE`.

## Resolving a gap the audit shows you (on the go)
- `MISSING_STATE J` → `<cmd>set J status <s>` (or `new` if it's brand new).
- `CLAIMED_DONE_NO_PROOF J` → the `done` job's `evidence:` path doesn't exist. Fix the path, or set
  `status` back if it isn't actually done.
- `BAD_STATUS / BAD_CATEGORY / BAD_PRIORITY / BAD_CADENCE J` → value not in the closed vocabulary.
  `set` it to a valid one.
- `STALE_<n>d J` → an `active` job untouched >14d. Finish it, block it, or mark it dead.
- `VERDICT_NO_STATUS J` → a `## Verdict` is written but status is non-terminal. `set` it done/dead.
