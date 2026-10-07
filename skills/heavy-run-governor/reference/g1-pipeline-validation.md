# G1 — Pipeline / transformation validation

The single most expensive class of mistake lives here: training on a tensor that
is not what you think it is. Fail-closed rule: **representation / model-input
validator fails → do not train.**

## The invariant chain — apply at EVERY important boundary

```
SOURCE → TRANSFORMATION → INDEPENDENT VALIDATOR → BLOCK / RELEASE
```

Validate each boundary *independently*. A validator that only checks the upstream
artifact does not certify the downstream tensor.

## Validate the ACTUAL model input — not merely the upstream artifact

The tensor handed to `model.forward(...)` is the thing that must be validated.
Everything that happens between your last checked artifact and the forward call is
an unvalidated boundary:

- normalization / standardization
- log / power transforms
- sentinel / NaN / missing-value handling
- masking, padding
- sequence packing, bucketing
- dt / time-delta scaling
- feature ordering / column reindexing
- dtype / precision casts

If any of these happen *after* your last validator, **add another validator at
that boundary.** Place a hook (or a one-batch dump) at the forward call and assert
the invariants there.

## "Same shape is not equivalence"

Matching shape and width proves nothing about:

- which feature is in which column (silent reorder),
- units/scale (raw vs normalized vs log),
- whether masked positions are truly ignored in the loss,
- whether labels are aligned to the right rows / timesteps,
- chronology (no future leakage into a past input).

Assert the *content* invariants, not just the geometry.

## What to assert at the model-input boundary

- shape AND meaning of each axis (batch, time, feature);
- feature order matches the declared contract;
- value ranges match the declared transform (e.g. normalized ⇒ ~0 mean/unit var);
- no NaN/Inf after sentinel handling;
- mask/padding positions are excluded from loss and from attention where intended;
- label alignment and chronology (decision-time features strictly precede the
  target horizon — no leakage);
- dtype/precision is what the optimizer expects.

A one-off script that dumps a single batch and checks these is cheap insurance
and belongs in the repo, not in your head.
