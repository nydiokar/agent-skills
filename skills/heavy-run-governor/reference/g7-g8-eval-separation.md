# G7–G8 — Dev/eval separation, honest negatives, reproduction

## Keep development and final evaluation separate

- **TRAIN** may be used freely for instrumentation (G2/G3).
- **Validation / internal validation** may guide pre-defined stopping and model
  selection (declared in G0).
- **Protected test / holdout must NOT repeatedly influence fixes.** Every repeated
  look at the test set turns it into another validation set and inflates the
  reported result.
- **Record every look / search move** against the holdout. The run manifest has a
  `holdout_looks` field for exactly this.

## Failed and broken runs stay in the ledger

Do not erase failed or broken runs. A bounded negative is a result; a hidden
negative is a lie by omission that lets the same dead lane be re-run later.

## No optimization treadmill (the G7 discipline)

If the instrument is valid (G1–G3 passed) and the frozen experiment is negative,
**accept the bounded negative.** Do not immediately launch LR sweeps, epoch
sweeps, architecture tournaments, feature searches, or loss-weight tuning to
rescue it. Further optimization is authorized ONLY when the programme explicitly
puts model/configuration search in scope — and then ASHA / Hyperband / pruning are
appropriate *because the search is intentional*, not as a backdoor rescue.

## Validity vs performance

- A model performing well does NOT prove the representation is correct (it may be
  exploiting a leak or a proxy).
- A model performing badly does NOT prove the information is absent (the
  representation or instrument may be the limit).

Prove data → representation → model input → optimizer/instrument FIRST. Only then
interpret predictive performance.

## G8 — Reproduction only when scientifically warranted

Reproduce when the result will drive a decision, when it is surprising, or when it
crosses a promotion gate — not reflexively. Reproduction uses the manifest hashes
(code/data/representation) so the rerun is the SAME experiment, not a near-miss.
