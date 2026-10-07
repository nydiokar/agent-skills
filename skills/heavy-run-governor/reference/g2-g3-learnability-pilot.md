# G2–G3 — Learnability gate + instrument-health pilot

Both gates qualify the INSTRUMENT, not the hypothesis. A failure here means the
rig is broken. Do not interpret science until both pass.

## Contents
- G2 Tiny-subset learnability (overfit test)
- G3 Short instrument-health pilot
- What a broken pilot means

## G2 — Tiny-subset learnability (overfit test)

Before hours of training, deliberately overfit a TINY TRAIN-only subset (e.g. a
handful of samples / one tiny batch). The model SHOULD be able to drive train loss
to near zero on it.

Fail-closed rule: **tiny-subset overfit fails → do not full-train.** If the model
cannot memorize a handful of samples, the instrument is not qualified.

When it fails, inspect in this order (these are the usual culprits):

- shapes (batch/time/feature mismatch),
- labels (wrong column, misaligned, all-constant),
- chronology (future leakage or reversed order),
- masking / padding (masked positions leaking into loss),
- sequence boundaries (samples bleeding across sequence edges),
- state reset (recurrent/stateful carryover not cleared between sequences),
- loss construction (reduction over masked positions, wrong target transform).

Fix the instrument here — this is minutes of compute, not hours.

## G3 — Short instrument-health pilot (TRAIN / internal-VAL only)

A short run on TRAIN + internal validation ONLY. Confirm the rig is numerically
healthy; do NOT yet interpret scientific performance.

Confirm:

- train loss decreases sensibly;
- no NaN / Inf anywhere;
- no obvious head domination (for multi-head models, check per-head loss, not just
  aggregate — aggregate loss can fall while a head is dead);
- gradients are numerically sane (finite, non-exploding, non-vanishing);
- masks / padding behave correctly;
- throughput is measurable (samples or states per second);
- memory stays bounded across the pilot.

Do NOT claim "gradient clipping stabilized it" merely because `clip_grad_norm_`
exists in the code — verify from the clip fraction and pre-clip grad norm that it
is actually doing something. Do NOT claim "all heads are healthy" because
aggregate loss decreased — check each head.

## A broken pilot means repair the instrument, not "the hypothesis failed"

If G2 or G3 fails, you have learned nothing about the hypothesis. Route back to G1
(bad model input) or the loss/optimizer construction. A negative scientific
verdict is only meaningful once the instrument is qualified.
