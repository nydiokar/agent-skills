# CLAUDE.md — rules for agents working IN the agent-skills registry

This repo is a **registry of portable skills** for agent-driven projects. You are either (a) adding
or editing a skill here, or (b) pulling a skill from here into another project. Read the right
section.

## Hard rules

- **A skill is PORTABLE or it does not belong here.** No hard-coded project paths, no `pnpm`/venv
  assumptions baked into a tool, no one-repo business logic. Project-specific knobs go in a per-skill
  config file the installer writes — never in the tool source.
- **`SKILLS_INDEX.md` is GENERATED.** Never hand-edit it. Run `python scripts/index_skills.py --render`.
  A pre-commit hook regenerates it and fails the commit if it is stale.
- **The selftest is the gate.** `python scripts/index_skills.py --selftest` must be GREEN before you
  commit a new or changed skill. It validates each manifest, checks that `SKILL.md` frontmatter
  `name` equals the directory name, and verifies index determinism.
- **One skill = one self-contained directory** under `skills/<name>/` with a `SKILL.md` + `skill.yaml`
  at its root. Everything the skill needs (tools, hooks, templates) lives inside that directory.
- **Do not break an installed skill's contract.** If you change a skill's tool interface, bump its
  `skill.yaml` `status` and note the change in its own README — downstream projects have copies.

## Adding / editing a skill

1. Start from the template: `cp -r skills/_TEMPLATE skills/<your-skill>`.
2. `SKILL.md` frontmatter: `name:` (== directory name) and `description:` (when-to-use, trigger
   phrases). This is what Claude Code reads to decide when to invoke the skill.
3. `skill.yaml`: fill `name`/`summary`/`kind`/`status`/`install` (required) + `tags`/`requires`/
   `entrypoints` (optional). See `CONTRIBUTING.md` for the key reference.
4. Keep any tool stdlib-only where you can; if it needs deps, state them in `requires:` and the
   skill's README.
5. `python scripts/index_skills.py --selftest` → GREEN. Commit.

## Pulling a skill into another project

- Copy the whole `skills/<name>/` directory into the target's `.claude/skills/<name>/`.
- Follow that skill's `SKILL.md` / `README` for tool + hook + command wiring.
- If the skill is an installer (its `SKILL.md` describes scaffolding), just invoke it in the target
  and let it wire itself.

## Where things live

| What you are about to write | Its home |
|---|---|
| A reusable agent capability | `skills/<name>/SKILL.md` + its own files |
| The registry entry for a skill | `skills/<name>/skill.yaml` |
| The catalog | `SKILLS_INDEX.md` — GENERATED, never hand-edit |
| How to contribute | `CONTRIBUTING.md` |
| A starting point for a new skill | `skills/_TEMPLATE/` |
| The index generator / validator | `scripts/index_skills.py` |
