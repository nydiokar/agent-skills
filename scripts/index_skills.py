#!/usr/bin/env python3
"""index_skills.py — generate SKILLS_INDEX.md from the skills/ directory.

THE REGISTRY CONTRACT
---------------------
Every skill is a self-contained directory under `skills/<name>/` that MUST contain:
  - SKILL.md        the Claude-Code skill entrypoint (YAML frontmatter: name, description)
  - skill.yaml      the registry manifest (see keys below)

It MAY contain anything else the skill needs (tool scripts, hooks, templates, docs).
A skill directory is PORTABLE: copying it into a target project's `.claude/skills/<name>/`
(plus wiring whatever its README says) is all it takes to use it.

skill.yaml keys
---------------
  name:        unique id, matches the directory name and SKILL.md frontmatter name
  summary:     one line — what it does (shown in the index)
  kind:        skill | skill+tool | skill+hook | bundle   (free-form label)
  status:      stable | beta | experimental
  install:     one line — how to install it into a target project
  tags:        [list, of, keywords]
  requires:    optional — runtime notes (e.g. "python3", "node")
  entrypoints: optional — list of notable files inside the skill dir

This tool reads every skills/*/skill.yaml, validates it against SKILL.md, and regenerates
SKILLS_INDEX.md (GENERATED — do not hand-edit). Pure stdlib (tiny YAML subset parser) so it
runs anywhere with Python 3, no pip install.

Subcommands:
  --render     regenerate SKILLS_INDEX.md
  --selftest   validate every skill (manifest present + well-formed + matches SKILL.md),
               then check render determinism. Exit 1 on any failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
INDEX = ROOT / "SKILLS_INDEX.md"

REQUIRED_KEYS = ("name", "summary", "kind", "status", "install")
VALID_STATUS = ("stable", "beta", "experimental")


def _parse_mini_yaml(text: str) -> dict:
    """Parse the tiny flat YAML subset skill.yaml uses: `key: value` and
    `key: [a, b, c]` and block lists under a key with `  - item` lines.
    Not a general YAML parser — deliberately small and dependency-free."""
    out: dict = {}
    cur_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # block-list item
        m = re.match(r"^\s+-\s+(.*)$", raw)
        if m and cur_key:
            out.setdefault(cur_key, [])
            if isinstance(out[cur_key], list):
                out[cur_key].append(_scalar(m.group(1)))
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", raw)
        if m:
            key, val = m.group(1), m.group(2).strip()
            cur_key = key
            if val == "":
                out[key] = []  # expect a block list to follow
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                out[key] = [_scalar(x.strip()) for x in inner.split(",") if x.strip()]
            else:
                out[key] = _scalar(val)
    return out


def _scalar(v: str):
    v = v.strip().strip('"').strip("'")
    return v


def _skill_md_name(skill_dir: Path) -> str | None:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _skill_md_desc(skill_dir: Path) -> str | None:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _load_skills() -> list[tuple[Path, dict]]:
    out = []
    if not SKILLS.is_dir():
        return out
    for d in sorted(SKILLS.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
            continue  # skip hidden + scaffolding dirs (e.g. _TEMPLATE)
        y = d / "skill.yaml"
        manifest = _parse_mini_yaml(y.read_text(encoding="utf-8")) if y.exists() else {}
        out.append((d, manifest))
    return out


def _validate(skill_dir: Path, m: dict) -> list[str]:
    errs: list[str] = []
    name = skill_dir.name
    if not (skill_dir / "SKILL.md").exists():
        errs.append(f"{name}: missing SKILL.md")
    if not (skill_dir / "skill.yaml").exists():
        errs.append(f"{name}: missing skill.yaml")
        return errs
    for k in REQUIRED_KEYS:
        if not m.get(k):
            errs.append(f"{name}: skill.yaml missing required key `{k}`")
    if m.get("status") and m["status"] not in VALID_STATUS:
        errs.append(f"{name}: bad status {m.get('status')!r} (want {VALID_STATUS})")
    if m.get("name") and m["name"] != name:
        errs.append(f"{name}: skill.yaml name {m['name']!r} != directory name")
    md_name = _skill_md_name(skill_dir)
    if md_name and md_name != name:
        errs.append(f"{name}: SKILL.md frontmatter name {md_name!r} != directory name")
    return errs


def cmd_render() -> int:
    skills = _load_skills()
    rows = []
    for d, m in sorted(skills, key=lambda x: x[0].name):
        name = d.name
        summary = m.get("summary") or (_skill_md_desc(d) or "")[:100]
        kind = m.get("kind", "skill")
        status = m.get("status", "?")
        tags = m.get("tags") or []
        tagstr = " ".join(f"`{t}`" for t in tags) if isinstance(tags, list) else str(tags)
        rows.append(f"| [`{name}`](skills/{name}/) | {status} | {kind} | {summary} | {tagstr} |")

    lines = [
        "# Skills Index (GENERATED — do not hand-edit)",
        "",
        "> Regenerate: `python scripts/index_skills.py --render`.",
        f"> {len(skills)} skill(s) registered.",
        "",
        "| Skill | Status | Kind | Summary | Tags |",
        "|---|---|---|---|---|",
    ]
    lines += rows
    lines += [
        "",
        "## Install any skill",
        "",
        "Each skill directory is self-contained. The usual install is:",
        "",
        "```bash",
        "# copy the skill into a target project's Claude-Code skills dir",
        "cp -r skills/<name> <target-project>/.claude/skills/<name>",
        "# then follow that skill's own README / SKILL.md for any tool/hook wiring",
        "```",
        "",
        "See each skill's `skill.yaml` `install:` line for its one-step install, and",
        "`CONTRIBUTING.md` to add a new skill.",
    ]
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[render] {len(skills)} skills -> {INDEX.relative_to(ROOT)}")
    return 0


def cmd_selftest() -> int:
    fails: list[str] = []
    skills = _load_skills()
    if not skills:
        fails.append("no skills found under skills/")
    for d, m in skills:
        fails += _validate(d, m)
    cmd_render()
    a = INDEX.read_text(encoding="utf-8")
    cmd_render()
    b = INDEX.read_text(encoding="utf-8")
    if a != b:
        fails.append("index not deterministic")
    if fails:
        print(f"[selftest] {len(fails)} FAIL")
        for f in fails[:40]:
            print("  - " + f)
        return 1
    print(f"[selftest] GREEN — {len(skills)} skills, index deterministic")
    return 0


def main(argv: list[str]) -> int:
    if "--render" in argv:
        return cmd_render()
    if "--selftest" in argv:
        return cmd_selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
