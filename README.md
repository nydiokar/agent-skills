# agent-skills

A durable, growing **registry of portable skills and tools** for agent-driven (Claude Code / LLM)
projects. Clone it, pull the skills you want into a project's `.claude/skills/`, and go. Each skill
is self-contained, documented, and installable on its own — this repo is the shelf they live on.

> **Status:** active. First skill landed: [`notes-system`](skills/notes-system/).
> The full catalog is in **[SKILLS_INDEX.md](SKILLS_INDEX.md)** (generated).

## What this is (and isn't)

- **Is:** a shared library of *reusable* agent capabilities — skills (Claude-Code `SKILL.md`),
  optionally bundled with their own tools, hooks, and templates. Each one is project-agnostic and
  carries its own install instructions.
- **Isn't:** a dumping ground for one project's bespoke scripts. If a skill only makes sense in one
  repo, it stays in that repo. A skill earns a place here by being *portable*.

## Layout

```
agent-skills/
  README.md             this file
  CLAUDE.md             rules for agents working IN this repo (read first)
  CONTRIBUTING.md       how to add a new skill (the contract + the checklist)
  SKILLS_INDEX.md       GENERATED catalog of all skills — regenerate, never hand-edit
  LICENSE
  skills/
    <skill-name>/       one self-contained skill per directory
      SKILL.md          the Claude-Code entrypoint (YAML frontmatter: name, description)
      skill.yaml        the registry manifest (summary / kind / status / install / tags)
      ...               any tool/hook/template/doc the skill needs
  skills/_TEMPLATE/     copy this to start a new skill
  scripts/
    index_skills.py     generates SKILLS_INDEX.md from skills/*/skill.yaml
```

## Use a skill in your project

```bash
git clone https://github.com/nydiokar/agent-skills.git
# copy the skill you want into your project's Claude-Code skills dir
cp -r agent-skills/skills/notes-system  <your-project>/.claude/skills/notes-system
# then follow that skill's SKILL.md / README for any tool + hook wiring
```

Many skills are *installers* — they scaffold themselves into your project when you invoke them.
`notes-system` is one: drop it in, invoke it, and it copies its tool + hook, scaffolds the folders,
wires the commands, and verifies. See each skill's `skill.yaml` `install:` line.

## Add a skill

Read **[CONTRIBUTING.md](CONTRIBUTING.md)**. The short version:

1. `cp -r skills/_TEMPLATE skills/<your-skill>`
2. Write `SKILL.md` (frontmatter `name` must equal the directory name) and fill `skill.yaml`.
3. `python scripts/index_skills.py --selftest` (must be GREEN — validates the manifest + regenerates the index).
4. Commit. The pre-commit hook re-renders `SKILLS_INDEX.md` and blocks a stale index.

## Maintenance

- **`SKILLS_INDEX.md` is generated.** Never hand-edit it; run `python scripts/index_skills.py --render`.
- **The selftest is the gate.** `python scripts/index_skills.py --selftest` validates every skill's
  manifest, checks `SKILL.md` frontmatter agreement, and verifies index determinism.
- **A skill is portable or it doesn't belong here.** No project-specific paths, deps, or assumptions
  baked into a skill's tool — push those into a per-skill config file instead.
