# G6 — Frozen full training: instrumentation, checkpointing, early stopping

The full run is authorized ONLY after G0–G5 pass. It must be observable,
resumable, and stopped on a frozen rule.

## Contents
- Minimum telemetry
- Checkpoint for real resume
- Early stopping done right
- Claims you may NOT make

## Minimum useful telemetry (emit a flushed line per epoch/step)

- train loss;
- validation loss;
- per-head train/validation loss (multi-head models);
- learning rate;
- pre-clip gradient norm p50 / p95;
- clipping fraction;
- samples / states per second;
- epoch wall time;
- data / forward / backward timing when available;
- peak RAM / VRAM;
- best epoch;
- best validation metric;
- epochs since improvement;
- early-stop reason.

"CPU/GPU is busy" is NOT proof of health — a wedged or mis-converging run also
burns compute. The log must answer both *is it alive and progressing* and *is the
loss sane / is it done*.

## Checkpoint for real resume

Long runs MUST write resumable checkpoints periodically (each epoch/interval) and
on graceful interruption where possible. Save at minimum:

- model state;
- optimizer state (NOT just the model — without it, resume is a warm-start, not a
  continuation);
- scheduler state if present;
- epoch / step;
- RNG / seed state where practical;
- early-stop state (best metric, epochs-since-improvement counter);
- best metric / best checkpoint pointer;
- run config;
- code / data / representation hashes.

On startup, resume from the checkpoint if present. Keeping `best_state` only in a
RAM variable means a kill at hour 15 loses all 15 hours.

Fail-closed rule: **checkpoint cannot resume → fix before relying on any long
run.** Verify resume ONCE on a small test (save → kill → restart → confirm it
continues, not restarts) rather than assuming it works.

## Early stopping done right

- Use a FROZEN validation metric (the one declared in G0).
- Define `min_delta`, `patience`, and best-checkpoint restoration.
- Tiny numerical improvements below `min_delta` must NOT reset patience forever.
- Do NOT change `patience` / epoch limits after seeing which way the result is
  moving — that is an experiment-design change (back to G0), not a rescue.
- Scheduler changes are experiment-design decisions, not emergency rescue moves.

## Claims you may NOT make without evidence

- "gradient clipping stabilized it" — only if the clip fraction + grad-norm
  telemetry show it acting.
- "all heads are healthy" — only from per-head loss, not aggregate.
- "the run is progressing" — only from a streaming log, not from CPU being busy.
