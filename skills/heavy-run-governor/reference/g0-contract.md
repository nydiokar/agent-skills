# G0 — Freeze the experiment contract (before any compute)

State all of the following *in writing* before G1. If any is blank, you are not
ready to spend compute. Never silently replace the intended target with a
convenient surrogate — a thresholded/proxy target is a contract change.

## The contract — fill every field

- **Question / hypothesis** — the one sentence this run exists to answer.
- **Estimand / target** — the EXACT quantity being predicted/estimated, including
  its construction (horizon, window, how the label is built from raw data).
- **Baseline** — what the result is compared against (flat, prior model, naive rule).
- **Primary metric + direction** — one metric, and whether higher or lower wins.
- **Train / validation / test split** — identities (ids, date ranges, seeds), and
  which data is allowed for *development*.
- **Positive / negative / inconclusive** — the pre-registered decision rule:
  what result counts as each. Decide this NOW, not after seeing the number.
- **Frozen during the run** — what is NOT allowed to change once G6 starts
  (patience, epochs, scheduler, feature set, loss weights, architecture).

## Surrogate-swap trap

If you find yourself about to train on "the closest available label" instead of
the estimand you declared, STOP. Either the estimand is wrong (amend the contract
explicitly and re-enter G0) or the surrogate is diagnostic-only and must not be
cited as the result. A thresholded surrogate standing in for a continuous estimand
is a governance violation, not a convenience.

## Positive / negative / inconclusive must be pre-registered

Pre-registration is what makes a negative result *bounded* rather than a prompt to
start the optimization treadmill (see SKILL.md → "No optimization treadmill"). If
the decision rule is written before the run, a negative is a finding; if it is
written after, it is motivated reasoning.
