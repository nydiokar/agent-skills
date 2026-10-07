# G4 — Runtime + memory profile (before optimizing or committing to the full run)

Optimize the MEASURED bottleneck, not a guess. Project the runtime before you
commit hours.

## Profile a representative slice first

Run a profiler over a representative slice before repeated multi-hour runs.
Attribute wall time to:

- preprocessing,
- data loading,
- Python / framework overhead,
- forward pass,
- backward pass,
- optimizer step,
- repeated deterministic encoding (a frequent hidden cost).

Then optimize whatever actually dominates.

## Detect accidental algorithmic waste (sequence models)

Explicitly inspect whether prefixes are being replayed unnecessarily. The causal
ideal is ONE pass that carries state forward:

```
X0 → h0 → X1 → h1 → ... → Xt → ht        # one causal pass, O(T)
```

Avoid, unless scientifically unavoidable:

```
for every t:  replay X0 .. Xt            # re-encodes the whole prefix, O(T²)
```

An O(T²) prefix replay can turn a 1-hour run into a 15-hour run invisibly. Check
for it before launching.

## Project runtime before committing

Record **states/sec** (or samples/sec) from the pilot, multiply by the full
dataset × epochs, and write down the expected finish time. A run with no projected
ETA cannot be told apart from a hung one.

## Optimize with measurement, not assumption

- Benchmark worker / thread counts rather than blindly increasing them (more
  workers can slow things via contention or memory pressure).
- Benchmark `torch.compile`, AMP / bfloat16, etc. only where they measurably help
  — do not assume they are free wins.
- If deterministic preprocessing repeats identically every epoch, consider a
  validated cache or a memory-mapped intermediate representation — but validate
  the cached tensor at the G1 model-input boundary (a cache is a new boundary).
