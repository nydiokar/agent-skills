---
name: heavy-run-governor
description: Governs expensive ML training and any long-running computation before it is allowed to spend hours of compute. Use before launching training runs, backfills, ETL, large scored comparisons, profiling sweeps, or any job expected to run longer than ~30 minutes, OR when the user mentions GPU/CPU/VRAM/ROCm/CUDA capacity, OOM, overfit/learnability checks, instrument-health pilots, gradient/loss instrumentation, checkpoint/resume, early stopping, run manifests, or "is the pipeline sane before I train". Enforces the rule that hours of compute are the LAST thing authorized, not the first thing tried.
---

# Heavy Run Governor

A heavy run is any job that will spend real wall-clock compute: ML training, a
backfill, an ETL, a large scored comparison, a profiling sweep — anything over
~30 min or anything that holds a GPU, pins RAM, or writes a big artifact.

**Overarching principle: hours of compute are the LAST thing authorized, not the
first thing tried.** Qualify the machine, the pipeline, the model input, and
learnability cheaply FIRST. Spend hours only after the cheap gates pass.

## The execution funnel — run gates in order, stop at the first failure

```
G0 Experiment contract        → freeze hypothesis/estimand/baseline/metric/splits
G1 Pipeline / transformation  → validate every boundary, esp. the actual model input
G2 Tiny-subset learnability   → overfit a tiny TRAIN-only sample
G3 Short health pilot         → loss↓, no NaN, heads/grads sane, memory bounded
G4 Runtime + memory profile   → find the real bottleneck; project runtime
G5 Resource preflight + GPU   → capacity + intended accelerator, no heavy contention
G6 Frozen full training       → checkpointed, instrumented, early-stopped
G7 Frozen evaluation          → protected test touched once
G8 Reproduction               → only when scientifically warranted
```

A later gate NEVER excuses skipping an earlier one. A broken pilot means **repair
the instrument, not "the hypothesis failed."**

## Fail-closed rules — these BLOCK the run, no exceptions

- Insufficient RAM/VRAM for the job → **do not start.**
- Another heavy workload risks OOM / thrashing / severe contention → **do not start**; record the conflicting process + the resources it holds.
- Intended GPU unavailable → **investigate before silently using CPU.** CPU fallback must be explicit and justified, never accidental.
- Representation / model-input validator fails → **do not train.**
- Tiny-subset overfit fails → **do not full-train.**
- NaN/Inf or stalled gradients → **stop and repair the instrument.**
- Output artifact missing at the end → the run is **not complete.**
- Checkpoint cannot resume → **fix before relying on any long run.**

## Gate playbooks (read the one you are on — each is one level deep)

| Gate | What it proves | Read |
|------|----------------|------|
| G0 | The experiment is frozen and honest before any compute | [reference/g0-contract.md](reference/g0-contract.md) |
| G1 | Every boundary — ESPECIALLY the tensor fed to the model — is validated independently | [reference/g1-pipeline-validation.md](reference/g1-pipeline-validation.md) |
| G2–G3 | The instrument is qualified: it can overfit a tiny sample, loss falls, heads/grads are sane | [reference/g2-g3-learnability-pilot.md](reference/g2-g3-learnability-pilot.md) |
| G4 | Time is spent where you think; projected runtime is known; no accidental O(n²) prefix replay | [reference/g4-profiling.md](reference/g4-profiling.md) |
| G5 | The machine has capacity and the intended accelerator is actually in use | [reference/g5-resource-preflight.md](reference/g5-resource-preflight.md) |
| G6 | The long run is instrumented, checkpointed, and early-stopped on a frozen metric | [reference/g6-training-instrumentation.md](reference/g6-training-instrumentation.md) |
| G7–G8 | Dev and final eval are separate; the holdout is not laundered into a validation set | [reference/g7-g8-eval-separation.md](reference/g7-g8-eval-separation.md) |
| — | What every serious run must leave behind | [reference/run-manifest.md](reference/run-manifest.md) |

## Scripts — RUN these, do not read their code (output-only, zero context cost)

Scripts are best-effort and cross-platform (Linux / WSL / macOS; degrade cleanly
on Windows-native). They print a verdict and exit non-zero on a fail-closed
condition, so they slot straight into a shell gate.

- **G5 preflight** — snapshot CPU load, RAM/swap, GPU/VRAM, disk, and running heavy processes; decide GO / NO-GO:
  ```bash
  python scripts/resource_preflight.py --need-ram-gb 16 --need-vram-gb 8 --need-disk-gb 20
  ```
- **G5 accelerator check** — prove the framework actually sees + will use the intended device (CUDA / ROCm-WSL / MPS), not just that a driver is installed:
  ```bash
  python scripts/accelerator_check.py --want cuda   # or: rocm | mps | cpu
  ```
- **G6/manifest** — emit the durable run manifest (ids, hashes, device, config, splits, timings) as JSON:
  ```bash
  python scripts/run_manifest.py --run-id <id> --out manifest.json
  ```

If a script cannot run on this machine, say so and fall through to the manual
checklist in the matching reference file — do not skip the gate.

## Two cheap gates that catch the most expensive mistakes

Everything below has been paid for in lost compute-hours. Encode permanently:

1. **Validate the ACTUAL model input, not the upstream artifact.** Same
   shape/width is not proof of equivalence. If normalization, log transforms,
   sentinel handling, masking, packing, dt scaling, or feature reordering happen
   *after* your last validator, add another validator at that boundary. (G1)
2. **Qualify learnability cheaply.** Overfit a tiny TRAIN-only subset before any
   hours-long run. If the model cannot memorize a handful of samples, the
   instrument is broken — fail here, not after 20 hours. (G2)

## No optimization treadmill

If the instrument is valid (G1–G3 passed) and the frozen experiment (G0) comes
back negative, **accept the bounded negative.** Do not immediately launch LR
sweeps, epoch sweeps, architecture tournaments, feature searches, or loss-weight
tuning to rescue it. Changing patience, epoch limits, or the scheduler *after*
seeing which way the result is moving is an experiment-design change, not a
rescue — it relaunches at G0. Configuration search (ASHA / Hyperband / pruning)
is in scope ONLY when the programme explicitly authorizes model search.

## Validity ≠ performance

A model performing well does not prove the representation is correct. A model
performing badly does not prove the information is absent. Prove the data,
representation, model input, and optimizer FIRST; interpret predictive
performance only after.
