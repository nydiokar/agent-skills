---
name: dispatch-system
description: Install or repair the portable, date-organized dispatch-state job tracker in a project. Use when the owner wants honest machine-tracked job state in ANOTHER project (or to re-scaffold/upgrade this one) — the folder-is-the-registry model with yaml state blocks, date-bucketed job folders (YYYY-MM), generated views, a warn-only git pre-commit trigger, and the audit/set/next/organize commands. Copies the tool, scaffolds the folder, migrates + organizes existing jobs, wires the git hook, and verifies with the tool's selftest. Idempotent.
---

# Scaffold the dispatch-state system into a project

You are installing a **portable, date-organized job-state tracker**. It replaces the common mess:
job state hand-maintained in prose that rots, and (if a dispatch folder already exists) a flat dump
of hundreds of `*.md` files in one unnavigable directory. You make state machine-tracked and the
folder date-organized, with zero ongoing friction, then hand back a one-line summary.

## The model (what you're installing)
- **The folder IS the registry.** Each job is `<dispatch>/<YYYY-MM>/<JOB_ID>.md` with a ` ```yaml `
  block at its top = that job's state (`status`, canonical `created_at`, `depends_on`, `evidence`,
  `results_ref`, optional `category`/`priority`/`cadence`).
- **Date-bucketed.** Jobs live in `YYYY-MM/` folders keyed off their canonical `created_at`
  (granularity configurable via `DISPATCH_BUCKET=year|month|day`, default `month`). Discovery is
  recursive, so every command works across buckets and a job is still addressed by bare `JOB_ID`.
- **Two generated views** (never hand-edited): `_DISPATCH_STATE.md` (eyeball) + `_dispatch.parquet`
  (query).
- **Trigger = git pre-commit** (backend-agnostic): renders the views + warns on gaps, never blocks.
- **The agent's only recurring action**: keep `status:` correct via `set`, and `new` to create.

## Steps (in order; each is idempotent)

1. **Locate the bundle.** This skill ships beside: `tool/dispatch_state.py`, `templates/CLAUDE.md`
   (the per-job protocol doc), `templates/.dispatch_not_a_job` (sidecar example),
   `usage-skill/SKILL.md` (the `dispatch` usage skill). Confirm they exist.

2. **Confirm the target + Python deps.** Decide the tool path (default
   `scripts/dispatch/dispatch_state.py`) and the dispatch dir (default `.ai/dispatch`). The tool
   needs Python with `pyyaml`, `pandas`, `pyarrow`. If any is missing, note it for the owner — the
   tool will not run without them.

3. **Copy the tool.** Copy `tool/dispatch_state.py` → `<target>/scripts/dispatch/dispatch_state.py`.
   The tool resolves `.ai/dispatch/` relative to its own location (`parents[2]`); to relocate, set
   `DISPATCH_DIR`. No path edits inside the tool.

4. **Create the dispatch dir if absent.** If `<target>/.ai/dispatch/` doesn't exist, create it (and
   ask the owner whether there are existing job briefs to import). Copy `templates/CLAUDE.md` →
   `<target>/.ai/dispatch/CLAUDE.md` (the auto-loaded protocol doc).

5. **Migrate existing jobs (if any).** Run `python scripts/dispatch/dispatch_state.py --migrate`.
   This injects a yaml block into every bare `*.md`, seeding `created_at` ONCE from each file's
   first git-commit date and inferring `status` from the file's own markers. It prints every guess.

6. **Spot-check + reconcile.** The inference is noisy — READ the migration output. For any row
   flagged `unknown`/`⚠`/obviously wrong, fix it via the tool (never hand-regex the block):
   `python scripts/dispatch/dispatch_state.py --set <JOB_ID> status <done|active|ready|blocked|dead>`.

7. **Organize into date-buckets.** Run `python scripts/dispatch/dispatch_state.py --organize`. This
   git-mv's every flat job file into its `created_at` `YYYY-MM/` bucket. Idempotent — safe to re-run.
   (If the owner prefers coarser/finer grouping, set `DISPATCH_BUCKET=year|day` first.)

8. **Project-specific non-jobs.** If the dispatch tree has `.md` files that aren't jobs (handoff
   addenda, specs, a README), add their filenames to `.ai/dispatch/.dispatch_not_a_job` (one per
   line) — see `templates/.dispatch_not_a_job`.

9. **Wire the git hook.** Run `python scripts/dispatch/dispatch_state.py --install-git-hook`. It
   appends a warn-only block to `.git/hooks/pre-commit` (idempotent; preserves an existing hook).

10. **Wire the commands + command-label.** If the target is a Node project, add `package.json`
    scripts: `dispatch:audit`, `dispatch:next`, `dispatch:set`, `dispatch:new`, `dispatch:organize`,
    `dispatch:render`, `dispatch:selftest` (each = `<python> scripts/dispatch/dispatch_state.py
    --<cmd>`). So the audit's fix-hints print the wrapper, set `DISPATCH_CMD` (e.g.
    `DISPATCH_CMD="pnpm dispatch:"`) in the environment the tool runs under. If not Node, tell the
    owner the raw `python scripts/dispatch/dispatch_state.py --<cmd>` invocations.

11. **Copy the usage skill.** Copy `usage-skill/SKILL.md` → `<target>/.claude/skills/dispatch/SKILL.md`
    so agents in the target can work the queue correctly.

12. **Add a pointer** in the target's root `CLAUDE.md`/`AGENTS.md` session-end section: state state is
    machine-tracked, the agent keeps `status:` correct via the dispatch tool, and the board
    (`audit`) is truth.

13. **Verify + report.** Run `python scripts/dispatch/dispatch_state.py --selftest` (must PASS) then
    `--audit`. Report: N jobs tracked, the date-buckets created, active/ready/blocked/done/dead
    counts, and any remaining gaps. Do NOT claim done if `--audit` still shows `unknown`/`BAD_STATUS`.

## Non-negotiables (carry these into the target)
- `created_at` is CANONICAL — seeded once, never re-derived. It also picks the date-bucket, so a
  job never hops buckets once filed.
- NEVER hand-regex a yaml block or hand-edit a generated view — use `set`.
- The git hook is WARN-ONLY — it must never block a commit.
- A job's verdict prose lives in its own `## Verdict` section, not a shared log.
