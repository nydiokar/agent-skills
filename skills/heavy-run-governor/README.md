# heavy-run-governor

A portable governor for expensive compute. Before any ML training run, backfill,
ETL, large scored comparison, or any job over ~30 minutes, this skill enforces one
principle:

> **Hours of compute are the LAST thing you authorize, not the first thing you try.**

It makes an agent qualify the machine, the pipeline, the *actual model input*, and
learnability — cheaply — before spending hours, and makes every long run
observable, resumable, and honestly evaluated.

## Install

```bash
cp -r skills/heavy-run-governor <target-project>/.claude/skills/heavy-run-governor
```

No wiring, no hook, no config file. Claude Code discovers it from the frontmatter
`description` and reads `SKILL.md` when a heavy run is imminent. The three scripts
run with **stdlib-only Python 3** (`torch` is optional — used only to verify the
accelerator and to snapshot the device in the manifest; absent `torch`, those
scripts degrade to a clear UNKNOWN instead of a false pass).

## What it does — the execution funnel

```
G0 Experiment contract        → freeze hypothesis/estimand/baseline/metric/splits
G1 Pipeline / transformation  → validate every boundary, esp. the ACTUAL model input
G2 Tiny-subset learnability   → overfit a tiny TRAIN-only sample
G3 Short health pilot         → loss↓, no NaN, heads/grads sane, memory bounded
G4 Runtime + memory profile   → find the real bottleneck; project runtime
G5 Resource preflight + GPU   → capacity + intended accelerator, no heavy contention
G6 Frozen full training       → checkpointed, instrumented, early-stopped
G7 Frozen evaluation          → protected test touched once
G8 Reproduction               → only when scientifically warranted
```

Run the gates in order; stop at the first failure. A later gate never excuses
skipping an earlier one. `SKILL.md` is the ~100-line entrypoint; each gate's
detailed playbook lives in `reference/<gate>.md` and loads only when that gate is
active (progressive disclosure — the whole corpus never loads at once).

## Scripts (run them; their code never enters context — only the verdict does)

| Script | Gate | What it proves | Exit codes |
|---|---|---|---|
| `scripts/resource_preflight.py` | G5 | capacity (RAM/VRAM/disk) + no heavy contention | `0` GO · `2` NO-GO (fail-closed) · `3` UNKNOWN |
| `scripts/accelerator_check.py` | G5 | the framework actually places a tensor on the intended device | `0` OK · `2` FAIL-CLOSED · `3` UNKNOWN |
| `scripts/run_manifest.py` | G6 | emits the durable, reproducible run manifest as JSON | `0` written · `2` blocking (unwritable) |

```bash
python scripts/resource_preflight.py --need-ram-gb 16 --need-vram-gb 8 --need-disk-gb 20
python scripts/accelerator_check.py --want cuda          # or: rocm | mps | cpu
python scripts/run_manifest.py --run-id <id> --out manifest.json
```

The scripts are cross-platform (Linux / WSL / macOS; RAM + disk gates also work on
Windows-native via a `ctypes` fallback). Anything a probe cannot measure is
reported UNKNOWN, never assumed healthy — the fail-closed posture is deliberate.

## Fail-closed rules (these BLOCK a run)

- Insufficient RAM/VRAM → do not start.
- Another heavy workload risks OOM/thrash/contention → do not start; record it.
- Intended GPU unavailable → investigate before silently using CPU.
- Model-input validator fails → do not train.
- Tiny-subset overfit fails → do not full-train.
- NaN/Inf or stalled gradients → stop and repair the instrument.
- Output artifact missing → the run is not complete.
- Checkpoint cannot resume → fix before relying on any long run.

## The two lessons that pay for the whole skill

1. **Validate the ACTUAL model input, not the upstream artifact.** Same
   shape/width is not proof of equivalence (G1).
2. **Qualify learnability cheaply** — overfit a tiny sample before hours of
   training; if it can't, the instrument is broken (G2).

Plus: instrument gradients/heads instead of inferring their health, profile before
optimizing, prefer the available GPU/ROCm path, never compete with another heavy
process for memory, and make every expensive run resumable.

## Portability

No project paths, no business logic, no framework lock-in. The gate doctrine is
plain markdown; the scripts are stdlib-only with optional `torch` detection. Drop
it into any agent-driven project and it works as-is.

status: **beta** — authored and script-verified; graduates to `stable` after it has
governed real runs in a second project.
