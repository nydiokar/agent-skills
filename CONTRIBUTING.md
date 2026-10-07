# Contributing a skill

This registry grows one portable skill at a time. A skill earns its place by being **reusable
across projects** — not by being useful in one. If it only makes sense in a single repo, keep it
there.

## The registry contract

Every skill is a self-contained directory `skills/<name>/` that MUST contain:

- **`SKILL.md`** — the Claude-Code entrypoint. YAML frontmatter with:
  - `name:` — unique id, **must equal the directory name**.
  - `description:` — when to use it + trigger phrases. Claude Code reads this to decide when to
    invoke the skill, so write it for discovery (what task, what words the user would say), not as a
    summary.
  - Body — the actual instructions the agent follows.
- **`skill.yaml`** — the registry manifest. Keys:

  | key | required | meaning |
  |---|---|---|
  | `name` | ✓ | unique id, matches directory + SKILL.md frontmatter name |
  | `summary` | ✓ | one line shown in the index |
  | `kind` | ✓ | `skill` \| `skill+tool` \| `skill+hook` \| `bundle` (free-form label) |
  | `status` | ✓ | `stable` \| `beta` \| `experimental` |
  | `install` | ✓ | one line — how to install into a target project |
  | `tags` | – | `[list, of, keywords]` |
  | `requires` | – | runtime notes, e.g. `python3`, `node` |
  | `entrypoints` | – | notable files inside the skill dir |

It MAY contain anything else the skill needs: tool scripts, hooks, templates, a README.

## Design rules (what makes a skill belong here)

- **Portable.** No hard-coded project paths or repo-specific business logic. Project-specific knobs
  live in a config file the skill's installer writes, not in the tool source.
- **Self-documenting.** The skill carries its own README / usage notes. A reader should be able to
  install and use it from the directory alone.
- **Thin deps.** Prefer stdlib-only tools. If you need third-party packages, declare them in
  `requires:` and the skill README, and justify them.
- **Honest status.** `experimental` until you've used it for real; `beta` once it works for you;
  `stable` once it's survived another project.

## Steps

```bash
cp -r skills/_TEMPLATE skills/<your-skill>
# edit skills/<your-skill>/SKILL.md      (frontmatter name == <your-skill>)
# edit skills/<your-skill>/skill.yaml    (fill the required keys)
# add your tool/hook/template files
python scripts/index_skills.py --selftest   # must be GREEN
git add skills/<your-skill> SKILLS_INDEX.md
git commit -m "add <your-skill> skill"
```

The pre-commit hook re-renders `SKILLS_INDEX.md` and fails if it would change (i.e. you forgot to
render). Run `python scripts/index_skills.py --render` and re-stage if it complains.

## Review checklist (self-review before you push)

- [ ] `SKILL.md` frontmatter `name` == directory name.
- [ ] `description:` names the task + trigger phrases a user would actually say.
- [ ] `skill.yaml` has all required keys; `status` is honest.
- [ ] No project-specific paths/deps baked into a tool; knobs are config-driven.
- [ ] The skill has its own README or clear usage notes.
- [ ] `--selftest` is GREEN; `SKILLS_INDEX.md` is up to date.
