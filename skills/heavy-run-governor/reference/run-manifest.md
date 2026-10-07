# Required run manifest

Every serious run must leave ONE durable record. Emit it with
`scripts/run_manifest.py` (see SKILL.md) or write the same JSON by hand. It is the
object that makes a run reproducible, auditable, and comparable.

## Fields

**Identity & inputs**
- `run_id`
- `git_commit`
- `data_hash`
- `representation_hash`
- `model_input_contract_hash`  (the G1 boundary contract — proves the tensor fed
  to the model was the intended one)

**Environment**
- `device` / `backend`
- `cpu`, `ram`, `gpu`, `vram`
- `precision`

**Configuration**
- `seed`
- `model_config`
- `optimizer_config`
- `stopping_config`  (`min_delta`, `patience`, metric, direction)

**Data governance**
- `train_id`, `val_id`, `test_id`  (split identities)
- `holdout_looks`  (count + log of every look at the protected test set — G7)

**Run accounting**
- `start_time`, `end_time`, `wall_time`
- `peak_memory`
- `throughput`  (samples or states / sec)

**Outputs**
- `checkpoint_paths`
- `best_checkpoint`
- `final_artifact_hashes`

**Verdict**
- `validator_results`  (G1/G2/G3 pass/fail)
- `final_scientific_status`  (positive / negative / inconclusive, per the G0 rule)

## Why each matters

The hashes let G8 reproduce the SAME experiment. `holdout_looks` is the honesty
ledger for G7. `final_scientific_status` is the pre-registered G0 verdict applied
to the actual number — not a post-hoc interpretation. A run whose output artifact
is missing is **not complete** regardless of what the log says.
