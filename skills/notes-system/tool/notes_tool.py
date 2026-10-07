#!/usr/bin/env python3
"""notes_tool.py — a portable shift-log store->projection engine.

THE MODEL (the thing you are porting)
-------------------------------------
A "shift log" is the handoff one agent/session leaves for the next. The failure mode
it replaces: every session dumps a dated prose blob into one always-read context file,
which a clock-based rule then evicts — so the file grows to tens of thousands of tokens
of history the next agent mostly doesn't need, and good notes get evicted by age, not
by relevance.

The fix is the same store->projection->guard pattern used for findings and dispatch:
  - STORE       : one note per handoff, a plain .md file under notes/<category>/,
                  filed by DURABLE category, with a small structured header.
  - PROJECTION  : this tool regenerates a single GENERATED router index from the
                  headers, and (optionally) refreshes a marked block in a context file.
  - GUARD       : the router is GENERATED — a hook denies hand-edits so it can't drift.
  - KEEP/ARCHIVE: SEMANTIC, never by clock. A note is `live` while re-reading it would
                  change the next agent's move; it settles to _archive/ VERBATIM when its
                  arc is closed and its durable content lives elsewhere. No cap, no FIFO.

This tool is deliberately project-agnostic:
  * categories + routing keywords live in notes/notes.config.json (NOT hardwired here),
  * the notes root is DISCOVERED by walking up from the tool to find the configured
    directory (default `.ai/notes`) — so it works regardless of repo layout,
  * no pnpm / virtualenv / third-party deps. Pure stdlib. Windows + POSIX safe.

SUBCOMMANDS
-----------
  --init        Scaffold the notes system in the current project: create the category
                folders, write notes.config.json (if absent) and the README/AGENTS docs
                (if absent). Idempotent.
  --render      Regenerate the router index from all note headers, and refresh the
                marked RECENT-NOTES block in the context file if the markers exist.
  --selftest    Header well-formedness + category validity + no-CI-in-body + router
                determinism (render twice, bytes must match). Exit 1 on any failure.
  --new "<title>" [--category <cat>]
                Create a new note file from the template with today's header pre-filled
                (date must be supplied with --date on systems where the clock is blocked;
                otherwise uses the OS date). Prints the path. Never overwrites.

Config resolution order for the notes root:
  1. $NOTES_ROOT if set (absolute or repo-relative),
  2. the nearest ancestor dir of CWD that contains `<notes_dir_name>/notes.config.json`,
  3. the nearest ancestor dir of CWD that contains `<notes_dir_name>/` (default `.ai/notes`),
  4. for --init only: `<CWD>/.ai/notes`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults — overridable entirely by notes.config.json so the tool is generic.
# ---------------------------------------------------------------------------
DEFAULT_NOTES_DIR_NAME = ".ai/notes"           # where notes live, relative to repo root
DEFAULT_CONTEXT_REL = ".ai/CONTEXT.md"         # file whose RECENT-NOTES block we refresh
DEFAULT_CONFIG_NAME = "notes.config.json"
ROUTER_NAME = "NOTES_INDEX_GENERATED.md"

# A sensible generic default taxonomy. Projects override via notes.config.json.
DEFAULT_CONFIG: dict = {
    "notes_dir": DEFAULT_NOTES_DIR_NAME,
    "context_file": DEFAULT_CONTEXT_REL,
    "recent_notes_count": 8,
    # FIRST match wins, in listed order. The LAST category is the default fallthrough.
    "categories": [
        {
            "name": "process",
            "blurb": "how WE work: tooling, repo infra, the notes/docs/CI machinery",
            "keywords": ["tooling", "ci", "pipeline", "repo", "docs", "notes system",
                         "scaffold", "workflow", "automation", "build system"],
        },
        {
            "name": "ops",
            "blurb": "the running system & infra: incidents, deploys, cost, leaks, outages",
            "keywords": ["incident", "deploy", "outage", "rollback", "latency", "cost",
                         "leak", "monitoring", "alert", "production", "hotfix"],
        },
        {
            "name": "decision",
            "blurb": "direction / frame / doctrine moves; a plan or a pivot (not an edge)",
            "keywords": ["decision", "direction", "pivot", "reframe", "roadmap", "plan",
                         "doctrine", "charter", "mission", "strategy change"],
        },
        {
            "name": "mechanism",
            "blurb": "a verified 'how a subsystem actually works', discovered mid-work",
            "keywords": ["how it works", "mechanism", "contract", "lifecycle", "invariant",
                         "subsystem", "data flow", "protocol"],
        },
        {
            "name": "work",
            "blurb": "the default: a unit of work's handoff (feature / fix / investigation)",
            "keywords": [],  # fallthrough default — keep LAST
        },
    ],
}

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
HEADER_KEYS = ("Status", "Category", "Date", "So-what", "Job", "Supersedes", "Ported-from")
HEADER_RE = re.compile(r"^(" + "|".join(HEADER_KEYS) + r"):\s*(.*)$")
# CI-in-body smell: a bracket pair of two numbers, e.g. [1.0853, 1.4268] or CI[.08,.21]
CI_SMELL = re.compile(r"(?:ci\s*)?\[\s*-?\d*\.?\d+\s*,\s*-?\d*\.?\d+\s*\]", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Root + config discovery
# ---------------------------------------------------------------------------
def _find_notes_root(notes_dir_name: str) -> Path | None:
    """Walk up from CWD to find an existing <notes_dir_name> directory."""
    env = os.environ.get("NOTES_ROOT")
    if env:
        p = Path(env)
        if p.exists():
            return p.resolve()
    start = Path.cwd().resolve()
    for base in (start, *start.parents):
        cand = base / notes_dir_name
        if cand.is_dir():
            return cand.resolve()
    return None


def _load_config(notes_root: Path | None) -> dict:
    """Load notes.config.json from the notes root, else return DEFAULT_CONFIG."""
    if notes_root is not None:
        cfg_path = notes_root / DEFAULT_CONFIG_NAME
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                # fill any missing top-level keys from defaults
                merged = {**DEFAULT_CONFIG, **data}
                return merged
            except Exception as e:  # noqa: BLE001
                print(f"[warn] bad {cfg_path}: {e} — using defaults", file=sys.stderr)
    return dict(DEFAULT_CONFIG)


def _category_names(cfg: dict) -> tuple[str, ...]:
    return tuple(c["name"] for c in cfg["categories"])


def _repo_root_for(notes_root: Path, cfg: dict) -> Path:
    """Infer repo root by stripping the configured notes_dir suffix from notes_root."""
    nd = Path(cfg.get("notes_dir", DEFAULT_NOTES_DIR_NAME))
    parts = len(nd.parts)
    root = notes_root
    for _ in range(parts):
        root = root.parent
    return root


# ---------------------------------------------------------------------------
# Note helpers
# ---------------------------------------------------------------------------
def _slugify(title: str) -> str:
    t = title.strip()
    m = re.match(r"[^A-Za-z0-9]*([A-Z0-9][A-Z0-9_]{4,})", t)
    if m:
        base = m.group(1).lower().replace("_", "-")
    else:
        words = re.findall(r"[a-zA-Z0-9]+", t.lower())[:6]
        base = "-".join(words) or "note"
    base = re.sub(r"-+", "-", base).strip("-")
    return base[:60] or "note"


def _categorize(cfg: dict, title: str, body: str) -> str:
    hay = (title + "\n" + body).lower()
    cats = cfg["categories"]
    for c in cats:
        kws = c.get("keywords") or []
        if kws and any(k.lower() in hay for k in kws):
            return c["name"]
    return cats[-1]["name"]  # last = default fallthrough


def _read_header(path: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()[:14]:
        m = HEADER_RE.match(ln)
        if m:
            d[m.group(1)] = m.group(2).strip()
    return d


def _note_body(path: Path) -> str:
    """Everything after the header block (used for CI-smell check)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # body begins after the last recognized header line within the first 14 lines
    last_hdr = 0
    for i, ln in enumerate(lines[:14]):
        if HEADER_RE.match(ln):
            last_hdr = i
    return "\n".join(lines[last_hdr + 1:])


def _all_notes(notes_root: Path, cfg: dict) -> list[tuple[Path, dict[str, str]]]:
    out: list[tuple[Path, dict[str, str]]] = []
    for c in _category_names(cfg):
        d = notes_root / c
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                out.append((p, _read_header(p)))
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_init() -> int:
    notes_dir_name = DEFAULT_CONFIG["notes_dir"]
    notes_root = _find_notes_root(notes_dir_name) or (Path.cwd() / notes_dir_name)
    notes_root = notes_root.resolve()
    notes_root.mkdir(parents=True, exist_ok=True)
    cfg = _load_config(notes_root)  # may be defaults

    # write config if absent
    cfg_path = notes_root / DEFAULT_CONFIG_NAME
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        print(f"[init] wrote {cfg_path}")
    else:
        print(f"[init] kept existing {cfg_path}")

    # create category folders + _archive, each with a .gitkeep
    for c in (*_category_names(cfg), "_archive"):
        d = notes_root / c
        d.mkdir(parents=True, exist_ok=True)
        gk = d / ".gitkeep"
        if not gk.exists():
            gk.write_text("", encoding="utf-8")
    print(f"[init] categories: {', '.join(_category_names(cfg))} (+ _archive)")

    # copy bundled docs/templates if they sit next to this tool's bundle
    bundle = Path(__file__).resolve().parent.parent
    for src_rel, dst in (
        ("templates/README.md", notes_root / "README.md"),
        ("templates/AGENTS.md", notes_root / "AGENTS.md"),
        ("templates/NOTE_TEMPLATE.md", notes_root / "NOTE_TEMPLATE.md"),
    ):
        src = bundle / src_rel
        if src.exists() and not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[init] wrote {dst.name}")

    cmd_render(notes_root, cfg)
    print(f"[init] DONE — notes root: {notes_root}")
    return 0


def cmd_render(notes_root: Path, cfg: dict) -> int:
    router = notes_root / ROUTER_NAME
    notes = _all_notes(notes_root, cfg)
    live = [(p, h) for p, h in notes if h.get("Status", "").startswith("live")]
    settled = [(p, h) for p, h in notes if not h.get("Status", "").startswith("live")]

    def _row(p: Path, h: dict[str, str]) -> str:
        rel = p.relative_to(notes_root).as_posix()
        return (f"| {h.get('Date','?')} | {h.get('Category','?')} | {h.get('Status','?')} | "
                f"{h.get('So-what','')[:140]} | `{rel}` |")

    lines = [
        "# NOTES — router index (GENERATED — do not hand-edit)",
        "",
        "> Regenerate: `python <path>/notes_tool.py --render`. Hand-edits are hook-denied.",
        f"> {len(live)} live · {len(settled)} settled · {len(notes)} total.",
        "",
        "## LIVE (still steer the next agent — read these)",
        "",
        "| Date | Cat | Status | So-what | File |",
        "|---|---|---|---|---|",
    ]
    lines += [_row(p, h) for p, h in sorted(live, key=lambda x: x[1].get("Date", ""), reverse=True)]
    lines += [
        "",
        "## SETTLED (history — greppable, not on the boot path)",
        "",
        "| Date | Cat | So-what | File |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {h.get('Date','?')} | {h.get('Category','?')} | {h.get('So-what','')[:120]} | "
        f"`{p.relative_to(notes_root).as_posix()}` |"
        for p, h in sorted(settled, key=lambda x: x[1].get("Date", ""), reverse=True)
    ]
    router.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[render] router: {len(live)} live, {len(settled)} settled -> {router}")

    # refresh the RECENT NOTES block in the context file if markers exist
    repo_root = _repo_root_for(notes_root, cfg)
    ctx = repo_root / cfg.get("context_file", DEFAULT_CONTEXT_REL)
    if ctx.exists():
        n = int(cfg.get("recent_notes_count", 8))
        text = ctx.read_text(encoding="utf-8", errors="replace")
        recent = sorted(live, key=lambda x: x[1].get("Date", ""), reverse=True)[:n]
        block = ["<!-- RECENT NOTES: generated by notes_tool.py --render, do not hand-edit -->"]
        for p, h in recent:
            try:
                rel = p.relative_to(repo_root).as_posix()
            except ValueError:
                rel = p.as_posix()
            block.append(f"- `{rel}` — {h.get('So-what','')[:120]}")
        block.append("<!-- END RECENT NOTES -->")
        new = "\n".join(block)
        text2 = re.sub(r"<!-- RECENT NOTES:.*?<!-- END RECENT NOTES -->", new, text, flags=re.DOTALL)
        if text2 != text:
            ctx.write_text(text2, encoding="utf-8")
            print(f"[render] refreshed RECENT NOTES block in {ctx}")
    return 0


def cmd_new(notes_root: Path, cfg: dict, title: str, category: str | None, date: str | None) -> int:
    if date is None:
        # OS clock; some sandboxes block this — then require --date
        try:
            import datetime
            date = datetime.date.today().isoformat()
        except Exception:  # noqa: BLE001
            print("[new] clock unavailable — pass --date YYYY-MM-DD", file=sys.stderr)
            return 2
    if not DATE_RE.match(date):
        print(f"[new] bad --date {date!r} (want YYYY-MM-DD)", file=sys.stderr)
        return 2
    cat = category or _categorize(cfg, title, "")
    if cat not in _category_names(cfg):
        print(f"[new] unknown category {cat!r}; valid: {', '.join(_category_names(cfg))}", file=sys.stderr)
        return 2
    slug = _slugify(title)
    target = notes_root / cat / f"{date}-{slug}.md"
    if target.exists():
        print(f"[new] exists, not overwriting: {target}", file=sys.stderr)
        return 1
    body = (
        f"# NOTE: {title}\n\n"
        f"Status: live\n"
        f"Category: {cat}\n"
        f"Date: {date}\n"
        f"Job: \n"
        f"Supersedes: \n"
        f"So-what: <ONE line — the router shows exactly this>\n\n"
        f"**What just happened.** \n\n"
        f"**What's hot.** \n\n"
        f"**Exact next task.** \n\n"
        f"**Watch out for.** \n\n"
        f"**Done and closed.** \n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    print(target)
    return 0


def cmd_selftest(notes_root: Path, cfg: dict) -> int:
    fails: list[str] = []
    cats = _category_names(cfg)
    notes = _all_notes(notes_root, cfg)
    if not notes:
        print("[selftest] GREEN — 0 notes (empty system is valid)")
        # still check determinism of an empty render
    for p, h in notes:
        for req in ("Status", "Category", "Date", "So-what"):
            if req not in h:
                fails.append(f"{p.name}: missing header {req}")
        if h.get("Category") not in cats:
            fails.append(f"{p.name}: bad category {h.get('Category')!r}")
        if h.get("Status", "") not in ("live", "settled"):
            fails.append(f"{p.name}: bad status {h.get('Status')!r}")
        if not DATE_RE.match(h.get("Date", "")):
            fails.append(f"{p.name}: bad date {h.get('Date')!r}")
        if CI_SMELL.search(_note_body(p)):
            fails.append(f"{p.name}: CI/number in body — belongs in the finding, not the note")
    # determinism: render twice, compare bytes
    router = notes_root / ROUTER_NAME
    cmd_render(notes_root, cfg)
    a = router.read_text(encoding="utf-8")
    cmd_render(notes_root, cfg)
    b = router.read_text(encoding="utf-8")
    if a != b:
        fails.append("router not deterministic")
    if fails:
        print(f"[selftest] {len(fails)} FAIL")
        for f in fails[:40]:
            print("  - " + f)
        return 1
    print(f"[selftest] GREEN — {len(notes)} notes, router deterministic")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(add_help=True, description="Portable shift-log notes tool")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--new", metavar="TITLE")
    ap.add_argument("--category", metavar="CAT", default=None)
    ap.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    args = ap.parse_args(argv)

    if args.init:
        return cmd_init()

    notes_dir_name = DEFAULT_CONFIG["notes_dir"]
    notes_root = _find_notes_root(notes_dir_name)
    if notes_root is None:
        print(f"[error] no {notes_dir_name}/ found above {Path.cwd()} — run --init first", file=sys.stderr)
        return 2
    cfg = _load_config(notes_root)

    if args.new:
        return cmd_new(notes_root, cfg, args.new, args.category, args.date)
    if args.render:
        return cmd_render(notes_root, cfg)
    if args.selftest:
        return cmd_selftest(notes_root, cfg)

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
