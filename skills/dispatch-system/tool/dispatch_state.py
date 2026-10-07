#!/usr/bin/env python
"""Dispatch state manager — honest, folder-is-the-registry job tracking.

This is a MANAGEMENT tool, not research. It imports nothing from research_os/.
The source of truth is the .ai/dispatch/ folder itself: every *.md dispatch
brief carries a small ```yaml state block at its top, and this script scans
them into two GENERATED views:

  _DISPATCH_STATE.md   — human-eyeball table (git-diffable)
  _dispatch.parquet    — machine-queryable table

State that an agent hand-edits lives in ONE place: the yaml block at the top of
each job's OWN file. Two agents on two jobs edit two files → no shared-table
contention. `created_at` is CANONICAL (written once at dispatch, never derived
from git). Only `updated_at` refreshes on edits.

Because the folder IS the registry, gaps are structurally detectable:
  - a *.md with no yaml block          → MISSING_STATE
  - status=done with an evidence path that doesn't exist on disk → CLAIMED_DONE_NO_PROOF
  - a written `## Verdict` but a non-terminal status → VERDICT_NO_STATUS (the MIRROR
    of CLAIMED_DONE_NO_PROOF: that one catches "says done but isn't proven", this one
    catches "is finished but doesn't say so")
  - status=blocked whose depends_on are ALL satisfied → STALE_BLOCKED
  - status=blocked with an EMPTY depends_on → BLOCKED_NO_GATE (parked behind nothing
    recorded; an unrecorded gate is not a satisfied gate, so it is NOT auto-clearable)
  - a row in the parquet with no file   → shows up as a removed row in git diff

TWO NOTIONS OF "BLOCKED" (the defect these flags close, 2026-08-17). `status: blocked`
is a STORED string that nothing ever writes back — `set_field` writes exactly the one
field you name, and there is no reverse pass that demotes blocked→ready when the last
dep goes done. `_unsatisfied_blockers()` is the real live computation, but it was only
ever called from `next_pick()`, which EXCLUDES status=="blocked" by construction — so
the one function that knew the truth was structurally prevented from being applied to
the only jobs that needed it. A job in the BLOCKED bucket was invisible to the resolver.
`audit()` echoed the raw `depends_on` list, which reads like an evaluated gate but is not.
Both flags below are now computed in `audit()`/`--stop-hook`/`render` via the same
`_unsatisfied_blockers()`, so audit and next can no longer disagree.

DATE-ORGANIZED LAYOUT (the 2026-10 improvement). Job files live in dated subfolders —
`<dispatch>/<bucket>/<JOB>.md`, where the bucket is derived from the job's CANONICAL
created_at (default granularity: `YYYY-MM`; override with DISPATCH_BUCKET=year|month|day).
This replaces the old flat dump of every job into one folder (unnavigable once it passes a
few hundred files). Discovery is RECURSIVE, so every command (audit/render/set/next) works
across buckets with zero change in how you call it; a job is still addressed by its bare
JOB_ID. `--new` files a fresh job into today's bucket; `--organize` moves existing flat (or
mis-bucketed) files into the bucket their created_at implies. Loose files at the dispatch
root are still read (back-compat), they are simply not organized until `--organize` runs.

Commands:
  --new ID    create a new dispatch job stub (canonical created_at stamped NOW), then render
  --set ID F V  safely edit one yaml field (the ONLY sanctioned state edit), then render
  --audit     scan + print the honest board (default)
  --render    (re)write _DISPATCH_STATE.md + _dispatch.parquet from the yaml blocks
  --organize  move job files into their created_at date-bucket (git-mv aware, idempotent)
  --stop-hook auto-render + print gaps ONLY (quiet if clean); for the Stop hook, fail-open
  --migrate   one-time: infer + inject a yaml block into every bare *.md, seeding
              created_at ONCE from the file's first git-commit date, then --render
  --selftest  teeth: round-trip, gap detection, canonical created_at immutability
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

# Windows consoles default to cp1252 and choke on ✓/⚠/emoji — force UTF-8 stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except (AttributeError, ValueError):
    pass

# PORTABLE: paths are relative to this script's location. Keep the script at
# <repo>/scripts/dispatch/dispatch_state.py and `.ai/dispatch/` resolves with
# ZERO config in any project that shares the layout. To relocate the dispatch
# folder, set DISPATCH_DIR env var (absolute or repo-relative).
REPO_ROOT = Path(__file__).resolve().parents[2]
_env_dir = __import__("os").environ.get("DISPATCH_DIR")
DISPATCH_DIR = (Path(_env_dir) if _env_dir and Path(_env_dir).is_absolute()
                else (REPO_ROOT / _env_dir) if _env_dir
                else REPO_ROOT / ".ai" / "dispatch")
STATE_MD = DISPATCH_DIR / "_DISPATCH_STATE.md"
STATE_PARQUET = DISPATCH_DIR / "_dispatch.parquet"

# files in the folder that are NOT dispatch jobs (logs, indices, protocol docs).
# The universal ones are hardcoded; project-specific exclusions go in a sidecar
# `.dispatch_not_a_job` (one filename per line) so this file stays portable.
NOT_A_JOB = {
    "DISPATCH_LOG.md",
    "_DISPATCH_STATE.md",
    "CLAUDE.md",             # the dispatch protocol doc (auto-loaded), not a job
    "AGENTS.md", "AGENT.md", "README.md",  # common non-job docs that may live here
}

def _load_sidecar_exclusions() -> set[str]:
    """Project-specific non-job filenames, one per line, in `.dispatch_not_a_job`
    inside the dispatch dir. Keeps per-project quirks OUT of this portable file."""
    sidecar = DISPATCH_DIR / ".dispatch_not_a_job"
    if not sidecar.exists():
        return set()
    return {ln.strip() for ln in sidecar.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")}


NOT_A_JOB |= _load_sidecar_exclusions()

VALID_STATUS = {"ready", "active", "blocked", "done", "dead"}
VALID_PRIORITY = {"P0", "P1", "P2", "P3"}
VALID_CATEGORY = {"research", "fix", "build", "ops", "charter"}
VALID_CADENCE = {"once", "recurring"}
FENCE = "```"
YAML_BLOCK_RE = re.compile(r"^```yaml\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class JobState:
    job_id: str
    file: str
    status: str
    created_at: str
    updated_at: str
    owner: str = ""
    depends_on: list[str] = field(default_factory=list)
    results_ref: str | None = None
    evidence: list[str] = field(default_factory=list)
    # RCA cost-accrual fields (yaml, optional). hours_spent seeds the RCA cost
    # denominator (next_experiment_oracle.cost_for_family); feature_family is the
    # ROBUST join key from an AUTO_ORACLE_* job back to its coarse family (so the
    # accrual reader never depends on de-slugging the job_id). Both default empty.
    hours_spent: float | None = None
    feature_family: str = ""
    # Taxonomy fields (yaml, optional — additive; old jobs without them are valid).
    category: str = ""      # research | fix | build | ops | charter
    priority: str = ""      # P0 | P1 | P2 | P3  (empty → treated as P2 in --next)
    cadence: str = ""       # once | recurring     (empty → treated as once)
    # derived (not stored in yaml):
    flags: list[str] = field(default_factory=list)


# ─────────────────────────────── scan ────────────────────────────────────────

# ── date-bucket config (the 2026-10 date-organization improvement) ──
# Bucket granularity for dated subfolders. `month` (YYYY-MM) is the default: coarse
# enough that a busy repo makes ~dozens of folders a year, fine enough to navigate.
_BUCKET = (__import__("os").environ.get("DISPATCH_BUCKET") or "month").strip().lower()
if _BUCKET not in {"year", "month", "day"}:
    _BUCKET = "month"

# How the fix-hints spell the command. Projects with a wrapper (pnpm/npm/make) set
# DISPATCH_CMD to it (e.g. "pnpm dispatch:"); otherwise hints show the raw portable
# invocation. The hint joins as "{_CMD}set ...", so the default ends in "--" with NO
# trailing space → "...dispatch_state.py --set JOB status done" reads as a real argv.
# A wrapper like "pnpm dispatch:" is used verbatim → "pnpm dispatch:set JOB ...".
_CMD = (__import__("os").environ.get("DISPATCH_CMD")
        or "python scripts/dispatch/dispatch_state.py --")


def _bucket_for(iso: str) -> str:
    """Date-bucket folder name for a canonical created_at ISO string.
    Falls back to 'undated' when the timestamp is missing/unparseable so a job is
    never silently dropped from organization."""
    s = (iso or "").strip().strip('"')
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return "undated"
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if _BUCKET == "year":
        return y
    if _BUCKET == "day":
        return f"{y}-{mo}-{d}"
    return f"{y}-{mo}"


# Subdir names under the dispatch dir that are NEVER scanned for jobs (generated views,
# archives, python caches). A date-bucket folder is any OTHER subdir.
_SKIP_DIRS = {"_archive", "__pycache__", ".git"}


def _job_files() -> list[Path]:
    """All job files, RECURSIVELY (so dated subfolders are discovered), excluding the
    non-job docs and the skip dirs. Addressing a job by bare JOB_ID still works because
    callers match on `.stem`."""
    out: list[Path] = []
    for p in DISPATCH_DIR.rglob("*.md"):
        if p.name in NOT_A_JOB:
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(DISPATCH_DIR).parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


def _as_list(v) -> list[str]:
    """Coerce a yaml value to a list of strings. A bare scalar (e.g. a single
    evidence path set via --set) becomes a one-element list, NOT char-split.

    COMMA-SPLIT (2026-07-27): a comma-separated scalar is split into paths. The
    documented multi-path form is a yaml list, but `--set <J> evidence "a.ts,b.json"`
    is the natural thing to type and used to be kept as ONE 90-char "path" — which
    never exists on disk, so a genuinely-finished job silently reported
    CLAIMED_DONE_NO_PROOF while every file was present. Failing that way is worse
    than the typo: it trains agents to distrust the audit. A real path cannot
    contain a comma here, so the split is unambiguous.
    """
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def _iso(v) -> str:
    """Normalize a yaml scalar (str or auto-parsed datetime) to ISO-with-T string."""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return str(v or "")


def _num(v) -> float | None:
    """Coerce a yaml scalar to float, or None (null / empty / unparseable). Used for
    hours_spent — a write-only-until-now field that must never crash the reader."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None  # reject NaN
    except (TypeError, ValueError):
        return None


def _read_yaml_block(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    m = YAML_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def scan() -> list[JobState]:
    """Read every job file's yaml block into a JobState, deriving flags."""
    jobs: list[JobState] = []
    for path in _job_files():
        data = _read_yaml_block(path)
        rel = path.relative_to(DISPATCH_DIR).as_posix()  # subfolder-aware link for the views
        if data is None:
            jobs.append(JobState(
                job_id=path.stem, file=rel, status="unknown",
                created_at="", updated_at="", flags=["MISSING_STATE"],
            ))
            continue
        js = JobState(
            job_id=str(data.get("job_id") or path.stem),
            file=rel,
            status=str(data.get("status", "unknown")),
            created_at=_iso(data.get("created_at")),
            updated_at=_iso(data.get("updated_at")),
            owner=str(data.get("owner", "")),
            depends_on=_as_list(data.get("depends_on")),
            results_ref=data.get("results_ref"),
            evidence=_as_list(data.get("evidence")),
            hours_spent=_num(data.get("hours_spent")),
            feature_family=str(data.get("feature_family", "") or ""),
            category=str(data.get("category", "") or ""),
            priority=str(data.get("priority", "") or ""),
            cadence=str(data.get("cadence", "") or ""),
        )
        js.flags = _derive_flags(js)
        jobs.append(js)
    # Second pass: flags that need the WHOLE set (dep resolution) or the file body.
    # Wired here, not in a caller, so audit/render/stop_hook/--next can never disagree.
    _derive_cross_job_flags(jobs)
    return jobs


def _derive_flags(js: JobState) -> list[str]:
    flags: list[str] = []
    if js.status not in VALID_STATUS:
        flags.append("BAD_STATUS")
    if js.status == "done" and js.evidence:
        missing = [e for e in js.evidence if not (REPO_ROOT / e).exists()]
        if missing:
            flags.append("CLAIMED_DONE_NO_PROOF")
    elif js.status == "done" and not js.evidence:
        # Distinct from CLAIMED_DONE_NO_PROOF (non-empty evidence with a missing path):
        # this fires when no evidence was listed at all — done with zero proof paths.
        # Scoped to jobs created on/after the DONE_NO_EVIDENCE flag was introduced
        # (2026-08-19). Jobs created before that predate the enforcement: the flag did
        # not exist when they were completed, so flagging them now is a false positive
        # (the 2026-09-04 backfill resolved the audit-noise wall; this floor prevents a
        # re-litigation of the same pre-convention legacy wall going forward).
        if not _pre_convention(js.created_at):
            flags.append("DONE_NO_EVIDENCE")
    # CLAIMED_DONE_NO_HOURS (case, 2026-08-30, the auto_oracle cost-accrual hole). A
    # TERMINAL (done/dead) AUTO_ORACLE_* job with null hours_spent silently feeds the
    # oracle's cost_for_family() STATIC fallback instead of a measured mean — the accrual
    # loop declared "closed" was functionally open (nobody fills hours_spent). This flags
    # the null going forward WITHOUT fabricating a number (the analogue of
    # CLAIMED_DONE_NO_PROOF: "says done but the cost accrual is missing"). Scoped to
    # AUTO_ORACLE_* because those are the jobs whose hours feed measured_family_cost_hours.
    if (js.status in TERMINAL_STATUSES and js.job_id.startswith("AUTO_ORACLE_")
            and js.hours_spent is None):
        flags.append("CLAIMED_DONE_NO_HOURS")
    if js.status in {"active", "ready", "blocked"} and js.created_at:
        age = _age_days(js.updated_at or js.created_at)
        if js.status == "active" and age is not None and age > 14:
            flags.append(f"STALE_{age}d")
    # Taxonomy validation (non-blocking; empty/absent is always valid — old jobs stay clean)
    if js.category and js.category not in VALID_CATEGORY:
        flags.append("BAD_CATEGORY")
    if js.priority and js.priority not in VALID_PRIORITY:
        flags.append("BAD_PRIORITY")
    if js.cadence and js.cadence not in VALID_CADENCE:
        flags.append("BAD_CADENCE")
    return flags


# A written verdict heading. Matches `## Verdict`, `### Verdict — DONE 2026-07-28`,
# `## ★ THE VERDICT IS IN (...)`, `## ═══ VERDICT CORRECTION ═══` — i.e. the word as a
# HEADING, with optional decoration before it. Surveyed against all 455 job files: the
# dominant form is a bare `## Verdict` (199), the rest carry a date/label suffix.
# Deliberately anchored to a heading: the word "verdict" in running prose ("awaiting a
# verdict", "the verdict below") must NOT fire this, or the flag becomes a firehose and
# agents learn to ignore the audit — the exact failure mode the comma-split bug caused.
TERMINAL_STATUSES = {"done", "dead"}
# `[^\w\n]{0,8}` absorbs decoration (★ ═══ — 🕐); `(?:the[ \t]+)?` absorbs the one real
# English variant found on disk ("## ★ THE VERDICT IS IN"). Anything longer is prose in a
# heading, not a verdict section, and is deliberately NOT matched.
VERDICT_HEADING_RE = re.compile(
    r"^\#{1,6}[ \t]*[^\w\n]{0,8}[ \t]*(?:the[ \t]+)?verdict\b",
    re.MULTILINE | re.IGNORECASE,
)


def _has_verdict_heading(path: Path) -> bool:
    """True if the job file carries a written `## Verdict` section."""
    try:
        return bool(VERDICT_HEADING_RE.search(path.read_text(encoding="utf-8")))
    except OSError:
        return False


def _derive_cross_job_flags(jobs: list[JobState]) -> None:
    """Second flag pass — the checks a SINGLE job cannot answer about itself.

    `_derive_flags` is per-job by construction: it sees one JobState and never the
    others' statuses nor the file body. These two need both, so they live here and are
    applied in-place after the scan. Every consumer (audit, render, stop_hook, --next)
    reads jobs from `scan()`, so wiring it there is what makes audit and next agree.

      STALE_BLOCKED     status=blocked but every depends_on entry resolves to done/dead.
                        Free-text gates ('owner-go:...') never resolve, so an owner-gated
                        job correctly stays parked and is NOT flagged.
      BLOCKED_NO_GATE   status=blocked with NO depends_on recorded. Distinct from
                        STALE_BLOCKED on purpose: an empty gate is an UNRECORDED gate, not
                        a satisfied one. Auto-labelling it "clearable" would invent a fact.
      VERDICT_NO_STATUS a `## Verdict` heading is written but status is not terminal.
                        The mirror of CLAIMED_DONE_NO_PROOF.
    """
    status_map: dict[str, str] = {j.job_id: j.status for j in jobs}
    for j in jobs:
        if "MISSING_STATE" in j.flags:
            continue  # nothing to reconcile — the yaml block itself is the gap
        if j.status == "blocked":
            if not j.depends_on:
                j.flags.append("BLOCKED_NO_GATE")
            elif not _unsatisfied_blockers(j, status_map):
                j.flags.append("STALE_BLOCKED")
        if j.status not in TERMINAL_STATUSES and _has_verdict_heading(DISPATCH_DIR / j.file):
            j.flags.append("VERDICT_NO_STATUS")


def _age_days(iso: str) -> int | None:
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
    except (ValueError, AttributeError):
        return None
    now = dt.datetime.now(dt.timezone.utc)
    return (now - d).days


# The DONE_NO_EVIDENCE flag (done but with zero proof paths) was introduced on
# 2026-08-19. Jobs created before that date predate the enforcement and are NOT
# flagged, so a pre-convention legacy wall cannot re-litigate the audit.
_EVIDENCE_FLAG_CONVENTION = dt.datetime(2026, 8, 19, tzinfo=dt.timezone.utc)


def _pre_convention(created_at: str) -> bool:
    if not created_at:
        return False
    try:
        d = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d < _EVIDENCE_FLAG_CONVENTION
    except (ValueError, AttributeError):
        # Unparseable creation date: treat as unverifiable and do NOT flag —
        # fail open rather than false-positive.
        return True


# ─────────────────────────────── render ──────────────────────────────────────

def to_frame(jobs: list[JobState]) -> pd.DataFrame:
    rows = [{
        "job_id": j.job_id, "file": j.file, "status": j.status,
        "created_at": j.created_at, "updated_at": j.updated_at, "owner": j.owner,
        "depends_on": ",".join(j.depends_on), "results_ref": j.results_ref or "",
        "evidence": ",".join(j.evidence), "flags": ",".join(j.flags),
        "category": j.category, "priority": j.priority, "cadence": j.cadence,
    } for j in jobs]
    df = pd.DataFrame(rows).sort_values(["status", "job_id"]).reset_index(drop=True)
    return df


STATUS_ORDER = {"active": 0, "ready": 1, "blocked": 2, "unknown": 3, "done": 4, "dead": 5}


def render(jobs: list[JobState]) -> None:
    df = to_frame(jobs)
    df.to_parquet(STATE_PARQUET, index=False)

    jobs_sorted = sorted(jobs, key=lambda j: (STATUS_ORDER.get(j.status, 9), j.job_id))
    lines = [
        "# Dispatch State — GENERATED, DO NOT EDIT",
        "",
        "<!-- regenerated by `python scripts/dispatch/dispatch_state.py --render`. "
        "DETERMINISTIC: output is a pure function of the yaml blocks — no wall-clock "
        "stamp, so an unchanged board re-renders byte-identical and git stays quiet. "
        "Source of truth = the ```yaml block at the top of each .ai/dispatch/<job>.md. "
        "Edit THAT, not this file. -->",
        "",
        "| status | job_id | created | updated | depends_on | proof | flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for j in jobs_sorted:
        proof = "—"
        if j.evidence:
            ok = all((REPO_ROOT / e).exists() for e in j.evidence)
            proof = "✓" if ok else "✗MISSING"
        dep = ",".join(j.depends_on) or "—"
        flags_parts = list(j.flags)
        if j.priority:
            flags_parts.insert(0, j.priority)
        if j.category:
            flags_parts.insert(0 if not j.priority else 1, j.category)
        flags = " ".join(flags_parts) or ""
        lines.append(
            f"| {j.status} | {j.job_id} | {j.created_at[:10] or '?'} | "
            f"{j.updated_at[:10] or '?'} | {dep} | {proof} | {flags} |"
        )
    STATE_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────────────── audit ───────────────────────────────────────

# The flags that constitute a REPORTABLE gap. Single definition so `audit` and
# `stop_hook` can never drift — adding a flag to one and forgetting the other is how
# a check ends up existing but never being seen.
PROBLEM_FLAGS = {
    "MISSING_STATE",
    "CLAIMED_DONE_NO_PROOF",
    "DONE_NO_EVIDENCE",
    "CLAIMED_DONE_NO_HOURS",
    "VERDICT_NO_STATUS",
    "STALE_BLOCKED",
    "BLOCKED_NO_GATE",
    "BAD_STATUS",
}


def _is_problem_flag(f: str) -> bool:
    return f in PROBLEM_FLAGS or f.startswith("STALE_") and f != "STALE_BLOCKED"


def _fix_hint(flag: str, j: JobState) -> str:
    """The concrete next command for each gap. A flag without a fix is a nag."""
    return {
        "MISSING_STATE": f"add a yaml block:  {_CMD}set {j.job_id} status <s>",
        "CLAIMED_DONE_NO_PROOF": f"add real evidence: paths in {j.file}, or set status back",
        "DONE_NO_EVIDENCE": (f"status=done but evidence: is empty — add artifact paths to "
                             f"{j.file}, or set status back if incomplete"),
        "CLAIMED_DONE_NO_HOURS": (f"AUTO_ORACLE job terminal but hours_spent: null — the oracle "
                                  f"cost loop falls back to STATIC cost. Fill real hours:  "
                                  f"{_CMD}set {j.job_id} hours_spent <n>  (do NOT fabricate)"),
        "VERDICT_NO_STATUS": (f"verdict is WRITTEN but status={j.status} — "
                              f"{_CMD}set {j.job_id} status done  (+ evidence: paths)"),
        "STALE_BLOCKED": (f"all deps satisfied — {_CMD}set {j.job_id} status ready"),
        "BLOCKED_NO_GATE": (f"blocked with NO depends_on — record the gate, or "
                            f"{_CMD}set {j.job_id} status ready"),
        "BAD_STATUS": f"{_CMD}set {j.job_id} status ready|active|blocked|done|dead",
    }.get(flag, f"review {j.file} — {flag}")


def audit(jobs: list[JobState]) -> int:
    n = len(jobs)
    with_state = sum(1 for j in jobs if "MISSING_STATE" not in j.flags)
    orphans = n - with_state
    by_status: dict[str, list[JobState]] = {}
    for j in jobs:
        by_status.setdefault(j.status, []).append(j)

    status_map: dict[str, str] = {j.job_id: j.status for j in jobs}

    print(f"{n} files · {with_state} with state · {orphans} orphans (MISSING_STATE)")
    print()
    for st in ["active", "ready", "blocked"]:
        js = by_status.get(st, [])
        print(f"  {st.upper():8} ({len(js)})")
        for j in sorted(js, key=lambda x: x.job_id):
            extra = f"  ⚠ {' '.join(j.flags)}" if j.flags else ""
            # EVALUATED gate, not a raw echo. This line used to print `dep:` with the raw
            # depends_on list, which reads like a live gate but is not — a job whose deps
            # were all done still showed them, so it looked permanently blocked and sat in
            # the BLOCKED bucket forever. Print only what is genuinely UNSATISFIED.
            unsat = _unsatisfied_blockers(j, status_map)
            if unsat:
                dep = f"  gate: {','.join(unsat)}"
            elif j.depends_on:
                dep = f"  gate: ✓all-satisfied ({len(j.depends_on)})"
            else:
                dep = ""
            print(f"      {j.job_id:34} created {j.created_at[:10] or '?'}{dep}{extra}")
    print(f"  DONE ({len(by_status.get('done', []))}) · "
          f"DEAD ({len(by_status.get('dead', []))})")
    print()

    problems = [(j, f) for j in jobs for f in j.flags if _is_problem_flag(f)]
    if problems:
        print("  ⚠ ATTENTION:")
        for j, f in problems:
            print(f"      {f:24} {j.job_id} ({j.file})  → {_fix_hint(f, j)}")
        return 1
    print("  ✓ no gaps, no unproven-done, no stale-active, "
          "no finished-but-unmarked, no stale-blocked")
    return 0


# ─────────────────────────────── migrate ─────────────────────────────────────

def _first_commit_iso(path: Path) -> str:
    """Canonical created_at seed: the file's FIRST commit date (once)."""
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--follow", "--format=%aI", "--", str(path)],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        ).stdout.strip().splitlines()
        if out:
            return out[-1]  # oldest add
    except (subprocess.SubprocessError, OSError):
        pass
    ts = path.stat().st_mtime
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def _infer_status(path: Path, log_section: dict[str, str]) -> tuple[str, str]:
    """Layered, most-authoritative-first. Returns (status, reason)."""
    text = path.read_text(encoding="utf-8")
    head = text[:1500]
    # 1) explicit marker inside the job's OWN file
    if re.search(r"❌|RETIRED|SUPERSEDED|KILLED|DEAD", head):
        return "dead", "own-file marker"
    if re.search(r"✅|DONE|COMPLETED|DELIVERED", head):
        return "done", "own-file marker"
    if re.search(r"🕐|BLOCKED|blocked on", head):
        return "blocked", "own-file marker"
    if re.search(r"🔵|READY|AUTHORED, not dispatched", head):
        return "ready", "own-file marker"
    if re.search(r"🟡|🟢|ACTIVE|LIVE|accruing|in progress", head):
        return "active", "own-file marker"
    # 2) weak fallback: DISPATCH_LOG section membership
    sec = log_section.get(path.name)
    if sec == "completed":
        return "done", "log-section (weak)"
    if sec == "dead":
        return "dead", "log-section (weak)"
    if sec == "active":
        return "active", "log-section (weak)"
    return "unknown", "NO SIGNAL — spot-check"


def _log_sections() -> dict[str, str]:
    t = (DISPATCH_DIR / "DISPATCH_LOG.md").read_text(encoding="utf-8")
    def pos(name: str) -> int:
        m = re.search(r"## .*?" + re.escape(name), t)
        return m.start() if m else -1
    bounds = [(n, pos(k)) for n, k in
              [("active", "Active + ready"), ("completed", "Completed"),
               ("dead", "Dead"), ("notopen", "Not-yet-open"), ("how", "How to update")]]
    bounds = sorted([b for b in bounds if b[1] >= 0], key=lambda x: x[1])
    out: dict[str, str] = {}
    for m in re.finditer(r"`([A-Za-z0-9_]+\.md)`", t):
        fn, p = m.group(1), m.start()
        for i, (name, b) in enumerate(bounds):
            nb = bounds[i + 1][1] if i + 1 < len(bounds) else len(t)
            if b <= p < nb:
                out.setdefault(fn, name)
                break
    return out


def set_field(job_id: str, field_name: str, value: str) -> None:
    """Safely edit ONE field in a job's yaml block via string replace on the value
    line (preserves comments + formatting); auto-bumps updated_at. This is the ONLY
    sanctioned way to mutate state — never hand-regex the blocks (that corrupts them)."""
    matches = [p for p in _job_files() if p.stem == job_id or p.name == job_id]
    if not matches:
        raise SystemExit(f"no dispatch file for job_id={job_id}")
    path = matches[0]
    text = path.read_text(encoding="utf-8")
    m = YAML_BLOCK_RE.search(text)
    if not m:
        raise SystemExit(f"{path.name} has no yaml block — run --migrate first")
    block = m.group(1)

    def replace_line(blk: str, key: str, val: str) -> str:
        # Keep any trailing "  # comment" on the line.
        # ⚠ A comment is only a comment when it is preceded by WHITESPACE (`  # note`). A bare `#`
        # inside a value is DATA — `results_ref: DISPATCH_LOG.md#SECTION` is an anchor, not a comment.
        # The old pattern (`[^#\n]*?`) split on the first `#`, so setting results_ref stored the value
        # as "DISPATCH_LOG.md" with comment "#SECTION", and the next --set re-appended the anchor
        # ("...md#SEC#SEC"). That silently corrupted 75 job files before it was caught (2026-07-28).
        # ⚠ `[ \t]*`, NOT `\s*`. `\s` matches NEWLINES, so on a block-sequence field the scalar
        # pattern happily matched "evidence:\n  - first-item" and rewrote just that much, orphaning
        # every REMAINING `- item` line under a now-scalar key. yaml then folded the lot into one
        # mangled string, and because it still PARSED the write-verify below said OK. Silent.
        pat = re.compile(rf"^({re.escape(key)}:[ \t]*)(\S(?:[^\n]*?\S)?)(\s+#.*)?$", re.MULTILINE)
        if pat.search(blk):
            return pat.sub(lambda mm: f"{mm.group(1)}{val}{mm.group(3) or ''}", blk, count=1)

        # BLOCK-SEQUENCE FORM — `evidence:` / `depends_on:` with the value on FOLLOWING lines:
        #     evidence:
        #       - a.ts
        #       - b.json
        # The scalar pattern above cannot match it (nothing follows the colon on that line), and
        # falling through to the append branch was a REAL corruption bug: `--set <J> evidence "…"`
        # appended a SECOND `evidence:` key at the end of the block while the original `- item`
        # lines stayed behind, so yaml folded the new scalar and the orphaned dashes into ONE
        # mangled string ("a.ts,b.json - src/… - src/…"). It still parsed, so the write-verify
        # below passed and the damage was silent — the same shape as the `#`-anchor bug above.
        # Replace the key line AND the indented sequence items it owns.
        seq = re.compile(
            rf"^({re.escape(key)}:)[ \t]*(\s+#[^\n]*)?\n((?:[ \t]+-[^\n]*\n?)+)",
            re.MULTILINE,
        )
        if seq.search(blk):
            return seq.sub(lambda mm: f"{mm.group(1)} {val}{mm.group(2) or ''}\n", blk, count=1)

        # Bare `key:` with an empty value and no items (e.g. a freshly stubbed `evidence:`).
        bare = re.compile(rf"^({re.escape(key)}:)[ \t]*$", re.MULTILINE)
        if bare.search(blk):
            return bare.sub(lambda mm: f"{mm.group(1)} {val}", blk, count=1)

        return blk.rstrip("\n") + f"\n{key}: {val}"

    new_block = replace_line(block, field_name, value)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    new_block = replace_line(new_block, "updated_at", f'"{now}"')
    original_text = text
    text = text[:m.start(1)] + new_block + text[m.end(1):]
    path.write_text(text, encoding="utf-8")
    # Verify it still parses (catch corruption immediately) and ACTUALLY REVERT if not.
    # ⚠ This used to write, detect the corruption, then exit saying "reverted needed" — WITHOUT
    # reverting, leaving the damaged block on disk for the next agent to trip over. A value
    # containing an unquoted ':' (e.g. "owner call: 2 rows stuck") reproduces it. The check is
    # worthless unless it restores; a guard that only NARRATES the damage is not a guard.
    if _read_yaml_block(path) is None:
        path.write_text(original_text, encoding="utf-8")
        raise SystemExit(
            f"⚠ edit would corrupt {path.name}'s yaml — REVERTED, file unchanged.\n"
            f"   Most likely the value needs quoting (a bare ':' or '#' breaks yaml): {value!r}"
        )
    print(f"{path.name}: {field_name} = {value} (updated_at bumped)")


def _inject_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if YAML_BLOCK_RE.search(text):
        return  # already has one, never clobber
    path.write_text(block + "\n\n" + text, encoding="utf-8")


def _state_block(job_id: str, created: str, status: str = "ready") -> str:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return (
        f"{FENCE}yaml\n"
        f"job_id: {job_id}\n"
        f"created_at: \"{created}\"        # CANONICAL — set once at dispatch, never derive again\n"
        f"status: {status}              # ready | active | blocked | done | dead\n"
        f"owner: \"\"\n"
        f"depends_on: []\n"
        f"results_ref: null             # -> DISPATCH_LOG.md section with the verdict prose\n"
        f"evidence: []                  # artifact paths that PROVE it ran (checked to exist)\n"
        f"hours_spent: null             # actual hours; feeds RCA cost_for_family once >=COST_MIN_JOBS/family filled\n"
        f"feature_family: \"\"            # coarse RCA family (AUTO_ORACLE_* jobs) — robust cost-accrual join key\n"
        f"category: \"\"                  # research | fix | build | ops | charter\n"
        f"priority: P2                  # P0 | P1 | P2 | P3  (within-category urgency)\n"
        f"cadence: once                 # once | recurring  (recurring = no terminal condition)\n"
        f"updated_at: \"{now}\"\n"
        f"{FENCE}"
    )


def new_job(job_id: str) -> None:
    """Create a new dispatch stub with a CANONICAL created_at (now). One command so
    nobody hand-writes a yaml block (that's how corruption crept in)."""
    job_id = job_id.removesuffix(".md")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    # File into the date-bucket for TODAY. Guard against a duplicate JOB_ID anywhere
    # in the tree (flat root or any bucket), not just the target path.
    existing = [p for p in _job_files() if p.stem == job_id]
    if existing:
        raise SystemExit(f"{existing[0].relative_to(DISPATCH_DIR).as_posix()} already exists "
                         f"— edit it, or --set its status")
    bucket_dir = DISPATCH_DIR / _bucket_for(now)
    bucket_dir.mkdir(parents=True, exist_ok=True)
    path = bucket_dir / f"{job_id}.md"
    body = (
        _state_block(job_id, now, status="ready") + "\n\n"
        f"# DISPATCH — {job_id}\n\n"
        "**Goal:** _(what the agent should do — one paragraph)_\n\n"
        "**Depends on:** _(blocker, or none)_\n\n"
        "## Task\n\n_(the concrete steps)_\n\n"
        "## Done when\n\n_(the deliverable + how it's proven — set `evidence:` to those paths)_\n"
    )
    path.write_text(body, encoding="utf-8")
    rel = path.relative_to(DISPATCH_DIR).as_posix()
    print(f"created {rel} (status: ready, created_at stamped now).")
    print(f"  → fill the brief, then work it. When done: --set {job_id} status done"
          " (and add evidence: paths).")


GIT_HOOK_MARKER = "# >>> dispatch-state pre-commit (managed, idempotent) >>>"
GIT_HOOK_END = "# <<< dispatch-state pre-commit <<<"

DOCTOR_HOOK_MARKER = "# >>> strategy:doctor pre-commit (managed, idempotent) >>>"
DOCTOR_HOOK_END = "# <<< strategy:doctor pre-commit <<<"


def install_git_hook() -> int:
    """Idempotently append a WARN-ONLY dispatch check to .git/hooks/pre-commit.
    Backend-agnostic (works in any git repo). Never blocks the commit; only
    renders the views + prints gaps. Re-running replaces the managed block.

    Also installs the strategy:doctor WARN block (non-blocking) in its own
    idempotent marker region after the dispatch block."""
    hooks_dir = REPO_ROOT / ".git" / "hooks"
    if not hooks_dir.exists():
        raise SystemExit("no .git/hooks — is this a git repo?")
    hook = hooks_dir / "pre-commit"
    dispatch_block = (
        f"{GIT_HOOK_MARKER}\n"
        "# Renders the dispatch state views + warns on gaps. Warn-only: never blocks.\n"
        "# Prefer the repo venv Python (has pandas); fall back to PATH python if the venv is absent.\n"
        'ROOT="$(git rev-parse --show-toplevel)"\n'
        'if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then\n'
        '  DISPATCH_PY="$ROOT/.venv/Scripts/python.exe"\n'
        'elif [ -x "$ROOT/.venv/bin/python" ]; then\n'
        '  DISPATCH_PY="$ROOT/.venv/bin/python"\n'
        'else\n'
        '  DISPATCH_PY="python"\n'
        'fi\n'
        '"$DISPATCH_PY" "$ROOT/scripts/dispatch/dispatch_state.py" --stop-hook || true\n'
        # _dispatch.parquet is NOT staged: it is gitignored (pyarrow bytes are
        # non-deterministic, so it can never be byte-stable) and is rebuilt on
        # every render. Staging it here is what kept the tree permanently dirty.
        'git add "$ROOT/.ai/dispatch/_DISPATCH_STATE.md" 2>/dev/null || true\n'
        f"{GIT_HOOK_END}\n"
    )
    doctor_block = (
        f"\n{DOCTOR_HOOK_MARKER}\n"
        "# Runs the strategy registry drift detector. Warn-only: never blocks.\n"
        "# Prints output so any class-A/B/I drift is visible in the commit log.\n"
        'DOCTOR_OUT=$(cd "$ROOT" && pnpm strategy:doctor 2>&1) || true\n'
        "DOCTOR_EXIT=$?\n"
        "if [ $DOCTOR_EXIT -ne 0 ]; then\n"
        "  echo \"\"\n"
        '  echo "[pre-commit] ⚠  strategy:doctor detected BLOCK-level drift (exit $DOCTOR_EXIT) — commit NOT blocked, but fix before deploy:"\n'
        '  echo "$DOCTOR_OUT"\n'
        "elif echo \"$DOCTOR_OUT\" | grep -q \"WARN\"; then\n"
        "  echo \"\"\n"
        '  echo "[pre-commit] ⚠  strategy:doctor WARN (visible gap, not blocking):"\n'
        '  echo "$DOCTOR_OUT"\n'
        "fi\n"
        f"{DOCTOR_HOOK_END}\n"
    )
    if hook.exists():
        text = hook.read_text(encoding="utf-8")
        # Replace dispatch block (idempotent)
        if GIT_HOOK_MARKER in text:
            text = re.sub(re.escape(GIT_HOOK_MARKER) + r".*?" + re.escape(GIT_HOOK_END) + r"\n?",
                          dispatch_block, text, flags=re.DOTALL)
        else:
            text = text.rstrip("\n") + "\n\n" + dispatch_block
        # Replace or append strategy:doctor block (idempotent)
        if DOCTOR_HOOK_MARKER in text:
            text = re.sub(re.escape(DOCTOR_HOOK_MARKER) + r".*?" + re.escape(DOCTOR_HOOK_END) + r"\n?",
                          doctor_block.lstrip("\n"), text, flags=re.DOTALL)
        else:
            text = text.rstrip("\n") + doctor_block
    else:
        text = "#!/bin/sh\n" + dispatch_block + doctor_block
    hook.write_text(text, encoding="utf-8")
    hook.chmod(0o755)
    print(f"installed dispatch+strategy:doctor pre-commit blocks into {hook} (warn-only, idempotent).")
    return 0


# Categories where an objective is sourced from a research lane and re-derivation
# actually happens. ops/build/fix jobs are concrete tickets — a manager does not
# "invent" them from prose, so nudging on them would be noise. This is the smart
# gate: the pick-from-dispatch:next reminder fires ONLY when the queue holds the
# kind of work a manager tends to bypass in favour of a prose "sense".
_PICK_NUDGE_CATEGORIES = {"research", "charter"}


def _pick_nudge(jobs: list[JobState]) -> str:
    """One-line reminder that the WORK PICK comes from `pnpm dispatch:next`, not
    prose — but ONLY when the ranked queue actually holds pickable research/charter
    work. Silent otherwise (no pickable lane → nothing to re-derive → no nudge).
    Never raises; the caller is fail-open regardless.

    This does NOT inspect what a worker was dispatched at (the Stop hook has no
    dispatch-event stream — guessing intent from the transcript is exactly the
    fragile, disruptive thing to avoid). It is a behavioural nudge keyed on QUEUE
    STATE, which cannot misfire on ops work and cannot misread intent. The
    mechanical re-derivation catch lives pre-dispatch in detect_rederivation();
    the `.ai/CLAUDE.md` boot loop is what forces it to be run.
    """
    pickable_lanes = [
        j for j in jobs
        if j.status in {"ready", "active"}
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
        and (j.category or "") in _PICK_NUDGE_CATEGORIES
    ]
    if not pickable_lanes:
        return ""
    n = len(pickable_lanes)
    return (
        f"↳ [dispatch] {n} pickable research/charter lane(s) in the ranked queue. "
        f"The WORK PICK comes from the ranked queue (`{_CMD}next`) — NOT a sense of what's valuable.\n"
        "  Sourcing an objective from PROSE instead of a ranked job? First prove the lane is "
        f"OPEN (`{_CMD}audit` — is it already a done job with a verdict?) before you re-run it."
    )


def _staged_dispatch_job_ids() -> set[str]:
    """Job ids whose .ai/dispatch/<ID>.md is part of the commit about to happen
    (staged, i.e. `git diff --cached --name-only`). This is the natural scope
    boundary for the rescue below: a job only just got a NEW evidence: path
    when its own file changed."""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return set()
    ids: set[str] = set()
    for line in out.splitlines():
        p = Path(line)
        if p.parent.as_posix() == ".ai/dispatch" and p.suffix == ".md":
            ids.add(p.stem)
    return ids


def _rescue_ignored_evidence(jobs: list[JobState]) -> list[str]:
    """A job's `evidence:` list is a declared commitment that those paths survive
    a clean checkout. evidence/atlas/ is blanket-gitignored (8.7GB+ of scratch
    research-run output with no filename rule separating it from a frozen
    result) — the intended mechanism is `git add -f` per frozen artifact, not
    widening the ignore. This closes that loop automatically instead of relying
    on every agent to remember it.

    Scope: only jobs whose dispatch .md is STAGED this commit (a job only
    gains a NEW evidence: entry when its own file is edited). Checking all
    ~600 jobs' ~1000 evidence paths on every commit was measured at ~14s/commit
    of near-total re-verification of paths already resolved on a prior commit
    — pure waste that grows unbounded with job count. Scoping to the commit's
    own staged jobs makes cost track commit size, not repo history size.
    One-time full-history backlog reconciliation already ran (2026-08-31,
    154 files rescued) — this is the steady-state incremental path, not the
    initial catch-up.

    Batches both `git check-ignore` and `git add -f` into single calls (each
    accepts many paths) instead of one subprocess pair per path. Warn-only,
    never raises — a broken hook must not trap a handoff."""
    staged_ids = _staged_dispatch_job_ids()
    if not staged_ids:
        return []
    candidates = sorted({p for j in jobs if j.job_id in staged_ids for p in j.evidence if p})
    existing = [rel for rel in candidates if (REPO_ROOT / rel).exists()]
    if not existing:
        return []
    try:
        check = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=REPO_ROOT, input="\0".join(existing), capture_output=True, text=True, timeout=15,
        )
        ignored = [p for p in check.stdout.split("\0") if p]
        if not ignored:
            return []
        subprocess.run(["git", "add", "-f", "--", *ignored],
                        cwd=REPO_ROOT, timeout=15, check=False)
        return ignored
    except (subprocess.SubprocessError, OSError):
        return []


def stop_hook() -> int:
    """Session-end hook: keep the views fresh + surface gaps, but NEVER block the
    session. Fail-open on any error (a broken hook must not trap a handoff)."""
    try:
        jobs = scan()
        render(jobs)
    except Exception as e:  # noqa: BLE001 — fail-open is the whole point
        print(f"[dispatch] render skipped ({type(e).__name__}) — state views may be stale.")
        return 0
    try:
        rescued = _rescue_ignored_evidence(jobs)
        if rescued:
            print(f"[dispatch] force-added {len(rescued)} gitignored evidence path(s) "
                  f"declared by a job (evidence/atlas/... force-add pattern):")
            for r in rescued:
                print(f"    + {r}")
    except Exception:  # noqa: BLE001 — fail-open, never trap a handoff
        pass
    gaps = [(j, f) for j in jobs for f in j.flags if _is_problem_flag(f)]
    if gaps:
        print("⚠ [dispatch] state has gaps — resolve on the go (does NOT block this session):")
        for j, f in gaps:
            print(f"    {f:22} {j.job_id:34} → {_fix_hint(f, j)}")
    try:
        nudge = _pick_nudge(jobs)
        if nudge:
            print(nudge)
    except Exception:  # noqa: BLE001 — fail-open, a nudge must never trap a handoff
        pass
    return 0


def migrate() -> None:
    log_section = _log_sections()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    report: list[tuple[str, str, str]] = []
    for path in _job_files():
        if YAML_BLOCK_RE.search(path.read_text(encoding="utf-8")):
            report.append((path.stem, "(has block)", ""))
            continue
        status, reason = _infer_status(path, log_section)
        created = _first_commit_iso(path)
        block = (
            f"{FENCE}yaml\n"
            f"job_id: {path.stem}\n"
            f"created_at: \"{created}\"        # CANONICAL — set once at dispatch, never derive again\n"
            f"status: {status}              # ready | active | blocked | done | dead\n"
            f"owner: \"\"\n"
            f"depends_on: []\n"
            f"results_ref: null             # -> DISPATCH_LOG.md section with the verdict prose\n"
            f"evidence: []                  # artifact paths that PROVE it ran (checked to exist)\n"
            f"hours_spent: null             # actual hours; feeds RCA cost_for_family once >=COST_MIN_JOBS/family filled\n"
            f"feature_family: \"\"            # coarse RCA family (AUTO_ORACLE_* jobs) — robust cost-accrual join key\n"
            f"category: \"\"                  # research | fix | build | ops | charter\n"
            f"priority: P2                  # P0 | P1 | P2 | P3  (within-category urgency)\n"
            f"cadence: once                 # once | recurring  (recurring = no terminal condition)\n"
            f"updated_at: \"{now}\"\n"
            f"{FENCE}"
        )
        _inject_block(path, block)
        report.append((path.stem, status, reason))
    print(f"migrated {len(report)} files:")
    for jid, status, reason in report:
        mark = "  " if reason in ("", "own-file marker") else "⚠ "
        print(f"  {mark}{status:9} {jid:38} {reason}")
    print("\nrendering views…")
    render(scan())
    print(f"wrote {STATE_MD.name} + {STATE_PARQUET.name}. SPOT-CHECK the 'unknown'/⚠ rows.")


# ─────────────────────────── organize (date-bucket) ─────────────────────────

def _git_mv(src: Path, dst: Path) -> bool:
    """Move src→dst via `git mv` so history follows the file; return True on success.
    Falls back to a plain filesystem move if git refuses (untracked file, no repo)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["git", "mv", "-k", str(src), str(dst)],
                            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and dst.exists() and not src.exists():
            return True
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        src.replace(dst)
        return True
    except OSError:
        return False


def organize() -> int:
    """Move every job file into the date-bucket its CANONICAL created_at implies.
    Idempotent: a file already in the right bucket is left untouched. created_at is
    NEVER changed — only the file's location. A job whose created_at is missing goes
    to the `undated/` bucket so it is still grouped, not lost."""
    moved = 0
    kept = 0
    skipped: list[str] = []
    for path in _job_files():
        data = _read_yaml_block(path)
        created = _iso(data.get("created_at")) if data else ""
        if not created:
            # try the git first-commit date as a last resort so undated files still bucket
            created = _first_commit_iso(path)
        bucket = _bucket_for(created)
        want = DISPATCH_DIR / bucket / path.name
        if path.resolve() == want.resolve():
            kept += 1
            continue
        if want.exists():
            skipped.append(f"{path.name}: target {bucket}/ already has a file of this name")
            continue
        if _git_mv(path, want):
            moved += 1
            print(f"  moved {path.relative_to(DISPATCH_DIR).as_posix()} -> {bucket}/{path.name}")
        else:
            skipped.append(f"{path.name}: move failed")
    print(f"[organize] moved {moved}, already-placed {kept}, skipped {len(skipped)} "
          f"(bucket granularity: {_BUCKET})")
    for s in skipped[:40]:
        print("  - " + s)
    print("\nrendering views…")
    render(scan())
    return 0


# ─────────────────────────────── next (ranked pick) ─────────────────────────

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_SATISFIED_STATUSES = {"done", "dead"}


def _build_reverse_dep_index(jobs: list[JobState]) -> dict[str, list[str]]:
    """Build a reverse-dependency index: job_id → list of job_ids that depend on it.
    O(N) over all depends_on lists. Covers all jobs regardless of status."""
    index: dict[str, list[str]] = {}
    for j in jobs:
        for dep in j.depends_on:
            index.setdefault(dep, []).append(j.job_id)
    return index


def _unsatisfied_blockers(j: JobState, status_map: dict[str, str]) -> list[str]:
    """Return depends_on entries that are NOT yet done/dead.

    A dep is satisfied iff it resolves to a known job_id in {done, dead}.
    Free-text gates (e.g. 'owner-go:...') never resolve → always unsatisfied,
    returned verbatim so the manager can see exactly what's blocking.
    """
    unsatisfied: list[str] = []
    for dep in j.depends_on:
        dep_status = status_map.get(dep)
        if dep_status not in _SATISFIED_STATUSES:
            # dep_status is None (free-text/unknown) or a non-done/dead status
            unsatisfied.append(dep)
    return unsatisfied


def next_pick(jobs: list[JobState]) -> int:
    """Print the ranked 'what should I pick up now' list, chain-aware.

    Inclusion: status in {ready, active} ONLY.
    Exclusion: blocked, done, dead (can't be picked), and cadence=recurring
               (monitors must not pollute the pick signal — the owner's core complaint).
    Sort: P0 < P1 < P2 < P3 (missing priority → P2), then by category (alphabetical
          within priority), then by staleness (older updated_at first as tie-break).
    Output: one line per job — Pn  category  job_id  (age in days) [unblocks:N] [blocked-by: X]

    After the ranked list, a PARKED section shows blocked jobs (non-recurring) with
    their dependency gates and unblocks counts, so the full chain is visible.
    The ranked pick order is unchanged — only annotation text is added.
    """
    # Build lookup maps once
    status_map: dict[str, str] = {j.job_id: j.status for j in jobs}
    rev_index = _build_reverse_dep_index(jobs)

    pickable = [
        j for j in jobs
        if j.status in {"ready", "active"}
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
    ]
    if not pickable:
        print("  (no pickable jobs — all are blocked/done/dead/recurring)")
    else:
        def _sort_key(j: JobState):
            pri = _PRIORITY_ORDER.get(j.priority or "P2", 2)
            cat = j.category or "zzz"  # unlabeled sorts last within priority
            age = _age_days(j.updated_at or j.created_at) or 0
            stale = -age  # older = smaller value → sorts first (most-stale first)
            return (pri, cat, stale)

        pickable.sort(key=_sort_key)

        current_pri = None
        for j in pickable:
            pri = j.priority or "P2"
            if pri != current_pri:
                current_pri = pri
                print(f"\n  ── {pri} ──")
            cat = j.category or "(unlabeled)"
            age = _age_days(j.updated_at or j.created_at)
            age_str = f"{age}d" if age is not None else "?"
            cad_mark = " ↻" if j.cadence == "recurring" else ""  # shouldn't appear, but guard
            # Chain annotations
            unblocks_n = len(rev_index.get(j.job_id, []))
            unsat = _unsatisfied_blockers(j, status_map)
            ann_parts: list[str] = []
            if unblocks_n > 0:
                ann_parts.append(f"[unblocks:{unblocks_n}]")
            if unsat:
                ann_parts.append(f"[blocked-by: {','.join(unsat)}]")
            ann = ("  " + " ".join(ann_parts)) if ann_parts else ""
            print(f"  {pri:<3}  {cat:<10}  {j.job_id:<52}  ({age_str}){cad_mark}{ann}")

        print(f"\n  {len(pickable)} pickable jobs (blocked/done/dead/recurring excluded)")

    # ── PARKED (blocked) section ──────────────────────────────────────────────
    parked = [
        j for j in jobs
        if j.status == "blocked"
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
    ]
    if parked:
        parked.sort(key=lambda j: (
            _PRIORITY_ORDER.get(j.priority or "P2", 2),
            j.job_id,
        ))
        print("\n  ── PARKED (blocked) ──")
        for j in parked:
            pri = j.priority or "P2"
            cat = j.category or "(unlabeled)"
            # EVALUATED gate, not a raw echo of depends_on. Printing the raw list here was
            # the other half of the defect: a job whose deps had ALL gone done still showed
            # them as its gate, so it read as permanently parked and was never picked up.
            unsat = _unsatisfied_blockers(j, status_map)
            unblocks_n = len(rev_index.get(j.job_id, []))
            ann_parts2: list[str] = []
            if unblocks_n > 0:
                ann_parts2.append(f"[unblocks:{unblocks_n}]")
            if "STALE_BLOCKED" in j.flags:
                ann_parts2.append("⚠ STALE_BLOCKED → set status ready")
            if "VERDICT_NO_STATUS" in j.flags:
                ann_parts2.append("⚠ VERDICT_NO_STATUS → set status done")
            ann2 = ("  " + " ".join(ann_parts2)) if ann_parts2 else ""
            if unsat:
                gate = ",".join(unsat)
            elif j.depends_on:
                gate = f"✓all-satisfied ({len(j.depends_on)}) — CLEARABLE"
            else:
                gate = "(no depends_on listed)"
            print(f"  {pri:<3}  {cat:<10}  {j.job_id:<52}  gate: {gate}{ann2}")

    return 0


# ─────────────────────────────── selftest ────────────────────────────────────

def selftest() -> int:
    import tempfile
    global DISPATCH_DIR, STATE_MD, STATE_PARQUET
    ok = True

    # 1) round-trip: a yaml block scans back to identical values
    blk = ("```yaml\njob_id: TEST_1\ncreated_at: 2026-01-01T00:00:00Z\n"
           "status: active\nowner: me\ndepends_on: [X]\nresults_ref: null\n"
           "evidence: []\nupdated_at: 2026-01-02T00:00:00Z\n```\n# brief\n")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "TEST_1.md"
        p.write_text(blk, encoding="utf-8")
        data = _read_yaml_block(p)
        assert data and data["status"] == "active" and data["depends_on"] == ["X"], data
    print("  ✓ round-trip: yaml block parses back exactly")

    # 2) gap detection: bare file → MISSING_STATE
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "BARE.md"
        p.write_text("# no yaml here\n", encoding="utf-8")
        assert _read_yaml_block(p) is None
    print("  ✓ gap detection: bare file yields no block (→ MISSING_STATE)")

    # 3) CLAIMED_DONE_NO_PROOF fires on a done job whose evidence is absent
    js = JobState("J", "J.md", "done", "2026-01-01", "2026-01-01",
                  evidence=["does/not/exist.parquet"])
    assert "CLAIMED_DONE_NO_PROOF" in _derive_flags(js)
    js2 = JobState("J", "J.md", "done", "2026-01-01", "2026-01-01", evidence=[])
    assert "CLAIMED_DONE_NO_PROOF" not in _derive_flags(js2)
    print("  ✓ proof guard: done+missing-evidence flags, done+no-claim does not")

    # 3b) DONE_NO_EVIDENCE: fires on done+empty-evidence; exclusive with CLAIMED_DONE_NO_PROOF
    #
    # Three assertions that prove the two flags are mutually exclusive by emptiness:
    #   (i)   done + NO evidence at all                  → DONE_NO_EVIDENCE (not CLAIMED_DONE_NO_PROOF)
    #   (ii)  done + a valid on-disk evidence path        → neither flag
    #   (iii) done + non-empty evidence with MISSING path → CLAIMED_DONE_NO_PROOF (not DONE_NO_EVIDENCE)
    import tempfile as _tempfile, os as _os
    with _tempfile.TemporaryDirectory() as _td:
        _real_path = _os.path.join(_td, "artifact.parquet")
        open(_real_path, "w").close()

        # (i) empty evidence → DONE_NO_EVIDENCE only (post-convention job)
        _js_none = JobState("J", "J.md", "done", "2026-09-01", "2026-09-01", evidence=[])
        _f_none = _derive_flags(_js_none)
        assert "DONE_NO_EVIDENCE" in _f_none, f"(i) expected DONE_NO_EVIDENCE, got {_f_none}"
        assert "CLAIMED_DONE_NO_PROOF" not in _f_none, \
            f"(i) CLAIMED_DONE_NO_PROOF must not co-fire when evidence is empty, got {_f_none}"

        # (ia) PRE-convention done + empty evidence → NOT flagged (the 2026-09-04
        # legacy-backfill floor: the flag did not exist when these jobs were created).
        _js_legacy = JobState("J", "J.md", "done", "2026-01-01", "2026-01-01", evidence=[])
        _f_legacy = _derive_flags(_js_legacy)
        assert "DONE_NO_EVIDENCE" not in _f_legacy, \
            f"(ia) pre-convention jobs must NOT fire DONE_NO_EVIDENCE, got {_f_legacy}"

        # (ii) done + real on-disk path → NEITHER flag
        _rel = _os.path.relpath(_real_path, str(REPO_ROOT))
        _js_ok = JobState("J", "J.md", "done", "2026-01-01", "2026-01-01", evidence=[_rel])
        _f_ok = _derive_flags(_js_ok)
        assert "DONE_NO_EVIDENCE" not in _f_ok, \
            f"(ii) DONE_NO_EVIDENCE must not fire when evidence exists on disk, got {_f_ok}"
        assert "CLAIMED_DONE_NO_PROOF" not in _f_ok, \
            f"(ii) CLAIMED_DONE_NO_PROOF must not fire when evidence exists on disk, got {_f_ok}"

        # (iii) done + non-empty evidence with MISSING path → CLAIMED_DONE_NO_PROOF (not DONE_NO_EVIDENCE)
        _js_miss = JobState("J", "J.md", "done", "2026-01-01", "2026-01-01",
                            evidence=["does/not/exist.parquet"])
        _f_miss = _derive_flags(_js_miss)
        assert "CLAIMED_DONE_NO_PROOF" in _f_miss, \
            f"(iii) expected CLAIMED_DONE_NO_PROOF on missing path, got {_f_miss}"
        assert "DONE_NO_EVIDENCE" not in _f_miss, \
            f"(iii) DONE_NO_EVIDENCE must not fire when evidence IS listed (but missing), got {_f_miss}"
    assert _is_problem_flag("DONE_NO_EVIDENCE"), "DONE_NO_EVIDENCE must reach operator surfaces"
    assert "review" not in _fix_hint("DONE_NO_EVIDENCE",
                                     JobState("J", "J.md", "done", "2026-01-01", "2026-01-01")), \
        "DONE_NO_EVIDENCE needs a real fix hint, not the fallback 'review ...'"
    print("  ✓ DONE_NO_EVIDENCE: fires on done+empty-evidence, silent on valid/missing paths "
          "(exclusive with CLAIMED_DONE_NO_PROOF)")

    # 3c) a comma-separated evidence scalar splits into paths (it used to be kept as ONE
    #     bogus 90-char path, so a finished job with every artifact present still reported
    #     CLAIMED_DONE_NO_PROOF — a false alarm that teaches agents to ignore the audit).
    assert _as_list("a.ts,b.json") == ["a.ts", "b.json"]
    assert _as_list("a.ts, b.json ") == ["a.ts", "b.json"]
    assert _as_list("solo.ts") == ["solo.ts"]
    assert _as_list(["x.ts", "y.json"]) == ["x.ts", "y.json"]
    assert _as_list("") == [] and _as_list(None) == []
    print("  ✓ evidence parse: comma scalar splits, single scalar and yaml list unchanged")

    # 4) canonical created_at immutability: injecting never clobbers an existing block
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "KEEP.md"
        p.write_text(blk, encoding="utf-8")
        _inject_block(p, "```yaml\ncreated_at: 2099-09-09T00:00:00Z\n```")
        assert _iso(_read_yaml_block(p)["created_at"]) == "2026-01-01T00:00:00+00:00"
    print("  ✓ canonical created_at: existing block never overwritten")

    # 5) bad status flagged
    assert "BAD_STATUS" in _derive_flags(JobState("J", "J.md", "nonsense", "", ""))
    print("  ✓ bad status flagged")

    # 6) set_field edits one value, keeps the comment, bumps updated_at, stays parseable
    saved = DISPATCH_DIR
    with tempfile.TemporaryDirectory() as d:
        DISPATCH_DIR = Path(d)
        p = Path(d) / "SETME.md"
        p.write_text("```yaml\njob_id: SETME\ncreated_at: \"2026-01-01T00:00:00Z\"\n"
                     "status: ready              # ready | active | ...\nowner: \"\"\n"
                     "depends_on: []\nresults_ref: null\nevidence: []\n"
                     "updated_at: \"2026-01-01T00:00:00Z\"\n```\n# brief\n", encoding="utf-8")
        set_field("SETME", "status", "done")
        d2 = _read_yaml_block(p)
        assert d2 and d2["status"] == "done", d2
        assert _iso(d2["created_at"]).startswith("2026-01-01"), "created_at must be untouched"
        assert _iso(d2["updated_at"]) != "2026-01-01T00:00:00Z", "updated_at must bump"
        assert "# ready | active" in p.read_text(encoding="utf-8"), "comment must survive"
    DISPATCH_DIR = saved
    print("  ✓ set_field: edits value, keeps comment, bumps updated_at, stays parseable")

    # 6b) set_field on a BLOCK-SEQUENCE field must REPLACE the whole sequence, not orphan its items.
    #     Regression tooth for a real silent corruption (2026-08-09): the scalar pattern used `\s*`
    #     after the colon, and `\s` matches NEWLINES — so on
    #         evidence:
    #           - a.ts
    #           - b.ts
    #     it matched "evidence:\n  - a.ts", rewrote only that, and left "  - b.ts" dangling under a
    #     now-scalar key. yaml folded them into ONE mangled string ("new,paths - b.ts"), which still
    #     PARSED, so the write-verify passed and the evidence was silently destroyed. Case 6 above
    #     could never catch it because it seeds the INLINE form (`evidence: []`), which the scalar
    #     pattern matches correctly. This seeds the form real job files actually use.
    saved = DISPATCH_DIR
    with tempfile.TemporaryDirectory() as d:
        DISPATCH_DIR = Path(d)
        p = Path(d) / "SEQME.md"
        p.write_text("```yaml\njob_id: SEQME\ncreated_at: \"2026-01-01T00:00:00Z\"\n"
                     "status: ready\nowner: \"\"\n"
                     "depends_on:\n  - \"dep one\"\n  - \"dep two\"\n"
                     "results_ref: null\n"
                     "evidence:\n  - old/a.ts\n  - old/b.ts\n  - old/c.json\n"
                     "updated_at: \"2026-01-01T00:00:00Z\"\n```\n# brief\n", encoding="utf-8")
        set_field("SEQME", "evidence", "new/x.ts,new/y.json")
        raw = p.read_text(encoding="utf-8")
        d3 = _read_yaml_block(p)
        assert d3 is not None, "block must still parse"
        assert _as_list(d3["evidence"]) == ["new/x.ts", "new/y.json"], _as_list(d3["evidence"])
        for stale in ("old/a.ts", "old/b.ts", "old/c.json"):
            assert stale not in raw, f"orphaned sequence item survived: {stale}"
        assert raw.count("evidence:") == 1, "a duplicate key was appended instead of replacing"
        # An UNTOUCHED sequence field must be left exactly as it was.
        assert _as_list(d3["depends_on"]) == ["dep one", "dep two"], d3["depends_on"]
    DISPATCH_DIR = saved
    print("  ✓ set_field: block-sequence field is REPLACED wholesale, no orphaned items")

    # 6c) A value that would BREAK the yaml must leave the file EXACTLY as it was. The guard used
    #     to write first, notice the damage, and exit with "reverted needed" — without reverting,
    #     so the corrupted block stayed on disk. A bare ':' in the value reproduces it.
    saved = DISPATCH_DIR
    with tempfile.TemporaryDirectory() as d:
        DISPATCH_DIR = Path(d)
        p = Path(d) / "BADVAL.md"
        good = ("```yaml\njob_id: BADVAL\ncreated_at: \"2026-01-01T00:00:00Z\"\n"
                "status: ready\nowner: \"\"\ndepends_on:\n  - \"dep one\"\n"
                "results_ref: null\nevidence: []\n"
                "updated_at: \"2026-01-01T00:00:00Z\"\n```\n# brief\n")
        p.write_text(good, encoding="utf-8")
        try:
            set_field("BADVAL", "depends_on", "owner call: two rows stuck")
        except SystemExit:
            pass
        else:
            raise AssertionError("an unquotable value must raise, not silently succeed")
        assert p.read_text(encoding="utf-8") == good, "failed edit must revert the file byte-exactly"
        assert _read_yaml_block(p) is not None, "file must still parse after a refused edit"
    DISPATCH_DIR = saved
    print("  ✓ set_field: a yaml-breaking value is REVERTED, not left on disk")

    # 7) a scalar evidence value coerces to a 1-element list (not char-split)
    assert _as_list("a/b.parquet") == ["a/b.parquet"]
    assert _as_list(["x", "y"]) == ["x", "y"]
    assert _as_list(None) == [] and _as_list("") == []
    print("  ✓ scalar evidence coerces to [path], not char-split")

    # 8) RCA cost-accrual fields: hours_spent + feature_family round-trip through scan();
    #    hours_spent coerces numerically (null/empty/garbage → None, never crash).
    blk_hrs = ("```yaml\njob_id: AUTO_ORACLE_OSCILLATING_STATEVAR\n"
               "created_at: \"2026-01-01T00:00:00Z\"\nstatus: done\nowner: \"\"\n"
               "depends_on: []\nresults_ref: null\nevidence: []\n"
               "hours_spent: 4.5\nfeature_family: \"oscillating-statevar\"\n"
               "updated_at: \"2026-01-02T00:00:00Z\"\n```\n# brief\n")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "AUTO_ORACLE_OSCILLATING_STATEVAR.md"
        p.write_text(blk_hrs, encoding="utf-8")
        dd = _read_yaml_block(p)
        js3 = JobState(
            job_id="AUTO_ORACLE_OSCILLATING_STATEVAR", file=p.name, status="done",
            created_at="2026-01-01", updated_at="2026-01-02",
            hours_spent=_num(dd.get("hours_spent")),
            feature_family=str(dd.get("feature_family", "") or ""))
        assert js3.hours_spent == 4.5, js3.hours_spent
        assert js3.feature_family == "oscillating-statevar", js3.feature_family
    assert _num(None) is None and _num("") is None and _num("x") is None
    assert _num("3") == 3.0 and _num(2.5) == 2.5
    print("  ✓ RCA cost fields: hours_spent + feature_family round-trip; _num degrades safely")

    # 9) taxonomy fields round-trip through scan() when present
    blk_tax = ("```yaml\njob_id: TAX_TEST\ncreated_at: \"2026-01-01T00:00:00Z\"\n"
               "status: ready\nowner: \"\"\ndepends_on: []\nresults_ref: null\nevidence: []\n"
               "category: research\npriority: P1\ncadence: once\n"
               "updated_at: \"2026-01-02T00:00:00Z\"\n```\n# brief\n")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "TAX_TEST.md"
        p.write_text(blk_tax, encoding="utf-8")
        dd = _read_yaml_block(p)
        js_tax = JobState(
            job_id="TAX_TEST", file=p.name, status="ready",
            created_at="2026-01-01", updated_at="2026-01-02",
            category=str(dd.get("category", "") or ""),
            priority=str(dd.get("priority", "") or ""),
            cadence=str(dd.get("cadence", "") or ""),
        )
        assert js_tax.category == "research", js_tax.category
        assert js_tax.priority == "P1", js_tax.priority
        assert js_tax.cadence == "once", js_tax.cadence
    print("  ✓ taxonomy fields: category/priority/cadence round-trip through read")

    # 10) defaults apply when fields absent — no BAD_* flags on old jobs
    js_old = JobState("OLD", "OLD.md", "ready", "2026-01-01", "2026-01-01")
    # category/priority/cadence all default to ""
    assert js_old.category == "" and js_old.priority == "" and js_old.cadence == ""
    flags_old = _derive_flags(js_old)
    assert "BAD_CATEGORY" not in flags_old
    assert "BAD_PRIORITY" not in flags_old
    assert "BAD_CADENCE" not in flags_old
    print("  ✓ defaults: absent taxonomy fields produce no BAD_* flags (old jobs stay clean)")

    # 11) BAD_PRIORITY / BAD_CATEGORY / BAD_CADENCE fire on bad values only
    js_bad = JobState("BAD", "BAD.md", "ready", "2026-01-01", "2026-01-01",
                      category="notacategory", priority="P9", cadence="weekly")
    flags_bad = _derive_flags(js_bad)
    assert "BAD_CATEGORY" in flags_bad, flags_bad
    assert "BAD_PRIORITY" in flags_bad, flags_bad
    assert "BAD_CADENCE" in flags_bad, flags_bad
    # valid values must NOT flag
    js_good = JobState("GOOD", "GOOD.md", "ready", "2026-01-01", "2026-01-01",
                       category="research", priority="P0", cadence="recurring")
    flags_good = _derive_flags(js_good)
    assert "BAD_CATEGORY" not in flags_good
    assert "BAD_PRIORITY" not in flags_good
    assert "BAD_CADENCE" not in flags_good
    print("  ✓ taxonomy validation: BAD_* fire on invalid values, not on valid ones")

    # 12) --next excludes recurring and blocked; sorts P0 before P1
    jobs_next = [
        JobState("ALPHA", "ALPHA.md", "ready", "2026-01-01", "2026-01-01",
                 category="research", priority="P1", cadence="once"),
        JobState("BETA", "BETA.md", "active", "2026-01-01", "2026-01-01",
                 category="fix", priority="P0", cadence="once"),
        JobState("GAMMA", "GAMMA.md", "active", "2026-01-01", "2026-01-01",
                 category="ops", priority="P1", cadence="recurring"),  # must be excluded
        JobState("DELTA", "DELTA.md", "blocked", "2026-01-01", "2026-01-01",
                 category="fix", priority="P0", cadence="once"),  # must be excluded
    ]
    pickable = [
        j for j in jobs_next
        if j.status in {"ready", "active"}
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
    ]
    assert len(pickable) == 2, pickable
    pickable.sort(key=lambda j: (
        _PRIORITY_ORDER.get(j.priority or "P2", 2),
        j.category or "zzz",
        -(_age_days(j.updated_at or j.created_at) or 0),
    ))
    assert pickable[0].job_id == "BETA", f"P0 fix must sort first, got {pickable[0].job_id}"
    assert pickable[1].job_id == "ALPHA", f"P1 research must sort second, got {pickable[1].job_id}"
    print("  ✓ --next: excludes recurring+blocked, sorts P0 before P1")

    # 13) chain-awareness: reverse-dep index + blocked-by + parked section
    #
    # Setup:  UPSTREAM (done) ← MIDDLE (ready) ← DOWNSTREAM_A (ready)
    #                                           ← DOWNSTREAM_B (blocked)
    #         FREETEXT_BLOCKED (blocked, depends on a free-text gate)
    #
    # (a) unblocks:N counts reverse-deps correctly — MIDDLE is depended on by 2 jobs
    # (b) a pickable job with an undone blocker shows blocked-by
    #     DOWNSTREAM_A is ready, depends_on=UPSTREAM (done) → no blocked-by (satisfied)
    # (c) a pickable job whose blockers are all done shows NO blocked-by
    #     MIDDLE is ready, depends_on=UPSTREAM (done) → no blocked-by
    # (d) blocked jobs appear in the parked section, not the ranked list
    #     DOWNSTREAM_B + FREETEXT_BLOCKED appear only in parked

    j_upstream = JobState("UPSTREAM", "UPSTREAM.md", "done", "2026-01-01", "2026-01-01",
                          category="build", priority="P1", cadence="once")
    j_middle = JobState("MIDDLE", "MIDDLE.md", "ready", "2026-01-01", "2026-01-01",
                        category="research", priority="P1", cadence="once",
                        depends_on=["UPSTREAM"])
    j_downstream_a = JobState("DOWNSTREAM_A", "DOWNSTREAM_A.md", "ready", "2026-01-01", "2026-01-01",
                              category="research", priority="P2", cadence="once",
                              depends_on=["MIDDLE"])
    j_downstream_b = JobState("DOWNSTREAM_B", "DOWNSTREAM_B.md", "blocked", "2026-01-01", "2026-01-01",
                              category="fix", priority="P2", cadence="once",
                              depends_on=["MIDDLE"])
    j_freetext = JobState("FREETEXT_BLOCKED", "FREETEXT_BLOCKED.md", "blocked", "2026-01-01", "2026-01-01",
                          category="ops", priority="P3", cadence="once",
                          depends_on=["owner-go:need manual approval"])

    chain_jobs = [j_upstream, j_middle, j_downstream_a, j_downstream_b, j_freetext]
    status_map_c = {j.job_id: j.status for j in chain_jobs}
    rev_idx = _build_reverse_dep_index(chain_jobs)

    # (a) MIDDLE is depended on by DOWNSTREAM_A and DOWNSTREAM_B → unblocks:2
    assert rev_idx.get("MIDDLE") == ["DOWNSTREAM_A", "DOWNSTREAM_B"] or \
        set(rev_idx.get("MIDDLE", [])) == {"DOWNSTREAM_A", "DOWNSTREAM_B"}, \
        f"MIDDLE reverse-deps wrong: {rev_idx.get('MIDDLE')}"
    assert len(rev_idx.get("MIDDLE", [])) == 2, \
        f"unblocks:N for MIDDLE must be 2, got {len(rev_idx.get('MIDDLE', []))}"
    print("  ✓ chain (a): unblocks:N counts reverse-deps correctly")

    # (b) DOWNSTREAM_A depends on MIDDLE (ready, not done) → blocked-by shown
    unsat_da = _unsatisfied_blockers(j_downstream_a, status_map_c)
    assert unsat_da == ["MIDDLE"], f"DOWNSTREAM_A blocked-by wrong: {unsat_da}"
    print("  ✓ chain (b): pickable job with undone blocker shows blocked-by")

    # (c) MIDDLE depends on UPSTREAM (done) → NO blocked-by
    unsat_mid = _unsatisfied_blockers(j_middle, status_map_c)
    assert unsat_mid == [], f"MIDDLE should have no blocked-by, got: {unsat_mid}"
    print("  ✓ chain (c): pickable job with all-done blockers shows NO blocked-by")

    # (d) blocked jobs appear in parked, not ranked
    chain_pickable = [
        j for j in chain_jobs
        if j.status in {"ready", "active"}
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
    ]
    chain_parked = [
        j for j in chain_jobs
        if j.status == "blocked"
        and j.cadence != "recurring"
        and "MISSING_STATE" not in j.flags
    ]
    pickable_ids = {j.job_id for j in chain_pickable}
    parked_ids = {j.job_id for j in chain_parked}
    assert "DOWNSTREAM_B" not in pickable_ids, "DOWNSTREAM_B must not be in ranked list"
    assert "FREETEXT_BLOCKED" not in pickable_ids, "FREETEXT_BLOCKED must not be in ranked list"
    assert "DOWNSTREAM_B" in parked_ids, "DOWNSTREAM_B must appear in parked"
    assert "FREETEXT_BLOCKED" in parked_ids, "FREETEXT_BLOCKED must appear in parked"
    # free-text gate: unsatisfied (resolves to no known job → always shown verbatim)
    unsat_ft = _unsatisfied_blockers(j_freetext, status_map_c)
    assert unsat_ft == ["owner-go:need manual approval"], \
        f"free-text gate must be shown verbatim in blocked-by, got: {unsat_ft}"
    print("  ✓ chain (d): blocked jobs appear in parked section; free-text gates shown verbatim")

    # 14) STALE_BLOCKED + BLOCKED_NO_GATE + VERDICT_NO_STATUS — the two-notions-of-blocked fix.
    #
    # REGRESSION TOOTH (2026-08-17). `status: blocked` is a STORED string nothing writes
    # back; `_unsatisfied_blockers` was the correct live computation but was ONLY called
    # from next_pick(), which excludes status=="blocked" by construction. So a job whose
    # deps had all gone done sat in the BLOCKED bucket forever, invisible to the resolver,
    # while audit() echoed its raw depends_on as if that were an evaluated gate. Three real
    # jobs carrying full PASS verdicts hid this way for a day and cascaded into a fourth.
    # These teeth assert the flags fire on the FAILING shapes and — just as important —
    # do NOT fire on the legitimate ones, or the audit becomes noise agents learn to ignore.
    saved = DISPATCH_DIR
    with tempfile.TemporaryDirectory() as d:
        DISPATCH_DIR = Path(d)

        def _mk(name: str, status: str, deps: str = "[]", body: str = "# brief\n") -> None:
            (Path(d) / f"{name}.md").write_text(
                f"```yaml\njob_id: {name}\ncreated_at: \"2026-01-01T00:00:00Z\"\n"
                f"status: {status}\nowner: \"\"\ndepends_on: {deps}\n"
                f"results_ref: null\nevidence: []\n"
                f"updated_at: \"2026-01-02T00:00:00Z\"\n```\n{body}",
                encoding="utf-8")

        _mk("DEP_DONE", "done")
        _mk("DEP_DEAD", "dead")
        _mk("DEP_OPEN", "ready")
        # (a) blocked, every dep satisfied (done AND dead both count) → STALE_BLOCKED
        _mk("STALE_ONE", "blocked", "[DEP_DONE, DEP_DEAD]")
        # (b) blocked with a genuinely open dep → must NOT flag
        _mk("REALLY_BLOCKED", "blocked", "[DEP_DONE, DEP_OPEN]")
        # (c) blocked behind a free-text owner gate → never resolves → must NOT flag
        _mk("OWNER_GATED", "blocked", "[\"owner-go:live re-arm\"]")
        # (d) blocked with NO depends_on → BLOCKED_NO_GATE, NOT STALE_BLOCKED (an
        #     unrecorded gate is not a satisfied gate — auto-clearing it invents a fact)
        _mk("NO_GATE", "blocked")
        # (e) a written verdict but a non-terminal status → VERDICT_NO_STATUS
        _mk("HAS_VERDICT", "ready", "[]", "# brief\n\n## Verdict\n\nPASS — it banks.\n")
        # (f) decorated/suffixed verdict headings must also fire
        _mk("VERDICT_SUFFIX", "active", "[]", "# b\n\n### Verdict — DONE 2026-07-28\n\nok\n")
        _mk("VERDICT_DECOR", "blocked", "[DEP_OPEN]", "# b\n\n## ★ THE VERDICT IS IN\n\nok\n")
        # (g) verdict + terminal status → correctly silent
        _mk("VERDICT_DONE", "done", "[]", "# brief\n\n## Verdict\n\nPASS.\n")
        # (h) the word "verdict" in PROSE, not a heading → must NOT fire (firehose guard)
        _mk("PROSE_ONLY", "ready", "[]", "# brief\n\nStill awaiting a verdict from the owner.\n")

        st = {j.job_id: j for j in scan()}

        assert "STALE_BLOCKED" in st["STALE_ONE"].flags, st["STALE_ONE"].flags
        assert "STALE_BLOCKED" not in st["REALLY_BLOCKED"].flags, st["REALLY_BLOCKED"].flags
        assert "STALE_BLOCKED" not in st["OWNER_GATED"].flags, st["OWNER_GATED"].flags
        assert "BLOCKED_NO_GATE" in st["NO_GATE"].flags, st["NO_GATE"].flags
        assert "STALE_BLOCKED" not in st["NO_GATE"].flags, \
            "an EMPTY depends_on is an unrecorded gate, not a satisfied one"

        assert "VERDICT_NO_STATUS" in st["HAS_VERDICT"].flags, st["HAS_VERDICT"].flags
        assert "VERDICT_NO_STATUS" in st["VERDICT_SUFFIX"].flags, st["VERDICT_SUFFIX"].flags
        assert "VERDICT_NO_STATUS" in st["VERDICT_DECOR"].flags, st["VERDICT_DECOR"].flags
        assert "VERDICT_NO_STATUS" not in st["VERDICT_DONE"].flags, \
            "a verdict on a done job is the NORMAL case and must stay silent"
        assert "VERDICT_NO_STATUS" not in st["PROSE_ONLY"].flags, \
            "prose mentioning 'a verdict' must not fire — heading-anchored only"

        # both flags must reach the operator surfaces, not just the dataclass
        assert _is_problem_flag("STALE_BLOCKED") and _is_problem_flag("VERDICT_NO_STATUS")
        assert _is_problem_flag("BLOCKED_NO_GATE")
        assert audit(list(st.values())) == 1, "audit must exit 1 when these gaps exist"
        assert stop_hook() == 0, "stop_hook must stay fail-open (warn, never block)"
        for f in ("STALE_BLOCKED", "VERDICT_NO_STATUS", "BLOCKED_NO_GATE"):
            assert "review" not in _fix_hint(f, st["STALE_ONE"]), f"{f} needs a real fix hint"
    DISPATCH_DIR = saved
    print("  ✓ STALE_BLOCKED: fires on all-deps-satisfied, silent on open + owner-gated deps")
    print("  ✓ BLOCKED_NO_GATE: empty depends_on is unrecorded, NOT auto-clearable")
    print("  ✓ VERDICT_NO_STATUS: fires on written-verdict + non-terminal, silent on prose/done")

    # 14b) _pick_nudge — fires on pickable research/charter, SILENT on ops/build/fix
    #      and on done/blocked/recurring. The smart gate: it must not nag when the
    #      only pickable work is a concrete ops ticket (no re-derivation risk there).
    def _j(jid, status, cat, cadence="once"):
        return JobState(job_id=jid, file="", created_at="2026-01-01T00:00:00Z",
                        updated_at="2026-01-02T00:00:00Z", status=status,
                        depends_on=[], results_ref=None, evidence=[],
                        category=cat, priority="P2", cadence=cadence)
    assert _pick_nudge([_j("R", "ready", "research")]) != "", \
        "nudge MUST fire on a pickable research lane"
    assert _pick_nudge([_j("C", "active", "charter")]) != "", \
        "nudge MUST fire on a pickable charter lane"
    assert _pick_nudge([_j("O", "ready", "ops"), _j("B", "ready", "build"),
                        _j("F", "ready", "fix")]) == "", \
        "nudge must be SILENT when only ops/build/fix are pickable (no re-derivation risk)"
    assert _pick_nudge([_j("D", "done", "research"), _j("K", "dead", "research")]) == "", \
        "nudge must be SILENT when research lanes are done/dead (not pickable)"
    assert _pick_nudge([_j("M", "ready", "research", cadence="recurring")]) == "", \
        "nudge must be SILENT on recurring monitors (must not pollute the pick signal)"
    print("  ✓ _pick_nudge: fires on pickable research/charter, silent on ops/build/fix + done/recurring")

    # 15) a job can carry BOTH flags at once (the exact shape that hid the three jobs:
    #     a finished verdict AND a stale block). Neither must mask the other.
    saved = DISPATCH_DIR
    with tempfile.TemporaryDirectory() as d:
        DISPATCH_DIR = Path(d)
        (Path(d) / "UP.md").write_text(
            "```yaml\njob_id: UP\ncreated_at: \"2026-01-01T00:00:00Z\"\nstatus: done\n"
            "owner: \"\"\ndepends_on: []\nresults_ref: null\nevidence: []\n"
            "updated_at: \"2026-01-02T00:00:00Z\"\n```\n# b\n", encoding="utf-8")
        (Path(d) / "BOTH.md").write_text(
            "```yaml\njob_id: BOTH\ncreated_at: \"2026-01-01T00:00:00Z\"\nstatus: blocked\n"
            "owner: \"\"\ndepends_on: [UP]\nresults_ref: null\nevidence: []\n"
            "updated_at: \"2026-01-02T00:00:00Z\"\n```\n# b\n\n## Verdict\n\nPASS.\n",
            encoding="utf-8")
        both = {j.job_id: j for j in scan()}["BOTH"]
        assert "STALE_BLOCKED" in both.flags and "VERDICT_NO_STATUS" in both.flags, both.flags
        # It must survive into the GENERATED view, not just the audit print. STATE_MD /
        # STATE_PARQUET are module constants resolved at import, so they must be redirected
        # too — otherwise this test would render over the REAL _DISPATCH_STATE.md with a
        # two-file temp registry. A test that corrupts the registry it is verifying is worse
        # than no test.
        saved_md, saved_pq = STATE_MD, STATE_PARQUET
        STATE_MD, STATE_PARQUET = Path(d) / "_DISPATCH_STATE.md", Path(d) / "_dispatch.parquet"
        try:
            render(scan())
            assert "STALE_BLOCKED" in STATE_MD.read_text(encoding="utf-8")
            assert "VERDICT_NO_STATUS" in STATE_MD.read_text(encoding="utf-8")
        finally:
            STATE_MD, STATE_PARQUET = saved_md, saved_pq
    DISPATCH_DIR = saved
    print("  ✓ both flags co-exist on one job and reach the generated view")

    print("\nSELFTEST PASS" if ok else "\nSELFTEST FAIL")
    return 0 if ok else 1


# ─────────────────────────────── cli ─────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Dispatch state manager (folder-is-the-registry).")
    ap.add_argument("--audit", action="store_true", help="scan + print the honest board (default)")
    ap.add_argument("--render", action="store_true", help="(re)write _DISPATCH_STATE.md + _dispatch.parquet")
    ap.add_argument("--migrate", action="store_true", help="one-time: inject yaml blocks + seed created_at, then render")
    ap.add_argument("--organize", action="store_true", help="move job files into their created_at date-bucket (git-mv aware, idempotent)")
    ap.add_argument("--set", nargs=3, metavar=("JOB_ID", "FIELD", "VALUE"),
                    help="safely edit one field in a job's yaml block (e.g. --set AGENT_15 status done); bumps updated_at + re-renders")
    ap.add_argument("--new", metavar="JOB_ID", help="create a new dispatch stub with a canonical created_at, then render")
    ap.add_argument("--stop-hook", action="store_true", help="session-end: auto-render + warn on gaps (fail-open, never blocks)")
    ap.add_argument("--install-git-hook", action="store_true", help="idempotently add a warn-only dispatch check to .git/hooks/pre-commit (any backend)")
    ap.add_argument("--next", action="store_true",
                    help="print ranked pick-next list: ready/active, non-recurring, sorted P0→P3 then category then staleness")
    ap.add_argument("--selftest", action="store_true", help="teeth")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.next:
        return next_pick(scan())
    if args.install_git_hook:
        return install_git_hook()
    if args.stop_hook:
        return stop_hook()
    if args.new:
        new_job(args.new)
        render(scan())
        return 0
    if args.set:
        set_field(*args.set)
        render(scan())
        return 0
    if args.migrate:
        migrate()
        return 0
    if args.organize:
        return organize()
    if args.render:
        render(scan())
        print(f"wrote {STATE_MD.name} + {STATE_PARQUET.name}")
        return 0
    return audit(scan())  # default


if __name__ == "__main__":
    sys.exit(main())
