# Skills Index (GENERATED — do not hand-edit)

> Regenerate: `python scripts/index_skills.py --render`.
> 2 skill(s) registered.

| Skill | Status | Kind | Summary | Tags |
|---|---|---|---|---|
| [`heavy-run-governor`](skills/heavy-run-governor/) | beta | skill+tool | Preflight + governor for heavy ML training and long-running jobs — an 8-gate funnel (contract → pipeline → learnability → pilot → profile → resource/GPU → training → eval) with fail-closed rules and runnable preflight scripts. | `ml` `training` `gpu` `preflight` `resource-governor` `checkpoint` `reproducibility` `long-running-jobs` |
| [`notes-system`](skills/notes-system/) | stable | bundle | Portable shift-log — one note per handoff, generated router, semantic keep/archive, guard hook. | `handoff` `shift-log` `context-management` `scaffolder` `hook` |

## Install any skill

Each skill directory is self-contained. The usual install is:

```bash
# copy the skill into a target project's Claude-Code skills dir
cp -r skills/<name> <target-project>/.claude/skills/<name>
# then follow that skill's own README / SKILL.md for any tool/hook wiring
```

See each skill's `skill.yaml` `install:` line for its one-step install, and
`CONTRIBUTING.md` to add a new skill.
