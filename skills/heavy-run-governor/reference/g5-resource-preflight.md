# G5 — Resource preflight + GPU selection

Preferred path: run `scripts/resource_preflight.py` and `scripts/accelerator_check.py`
(see SKILL.md). This file is the MANUAL fallback when the scripts cannot run, plus
the decision rules the scripts encode.

## Contents
- Preflight the machine
- Prefer GPU; verify it is actually used
- WSL GPU / ROCm path
- Fail-closed decisions

## Preflight the machine (before any heavy run)

1. **Inspect running heavy processes FIRST.** What is already consuming CPU, RAM,
   or VRAM? A new heavy job must not compete with an existing one for memory.
2. Check: CPU load, RAM free/available, swap pressure, GPU utilization + VRAM
   usage, disk free space.
3. If another heavy job is consuming enough to risk OOM, thrashing, or severe
   contention → **do not start.** Record the conflicting process (pid, command)
   and the resources it holds.

Manual commands (Linux / WSL):
```bash
uptime                       # load average
free -h                      # RAM + swap
df -h .                      # disk free on the working volume
nvidia-smi                   # NVIDIA GPU + VRAM + processes
rocm-smi                     # AMD/ROCm GPU + VRAM
ps aux --sort=-%mem | head   # top memory consumers (candidate conflicts)
```

## Prefer GPU; verify the framework actually uses it

Prefer GPU execution whenever the workload supports it. **Do not assume the GPU
is used because ROCm/CUDA is installed.** Verify the framework sees AND selects
the intended device, and record: device, backend, precision, VRAM, thread config.

```python
import torch
print(torch.cuda.is_available(), torch.cuda.device_count())   # CUDA or ROCm-via-HIP
print(torch.backends.mps.is_available())                      # Apple MPS
# place a tensor and confirm it is actually on the device you intend:
x = torch.ones(1).to("cuda"); print(x.device)
```

## WSL GPU / ROCm path (this environment specifically)

On WSL, check the WSL GPU / ROCm path before falling back to CPU. A ROCm build
exposes the GPU through HIP, so `torch.cuda.is_available()` may still be the right
probe. Confirm `rocm-smi` sees the card and that a placed tensor reports a GPU
device. If the intended GPU is unavailable, **investigate before silently using
CPU** — CPU fallback must be explicit and justified, never accidental.

## Fail-closed decisions

- Insufficient RAM/VRAM for the declared need → **do not start.**
- Conflicting heavy workload risking OOM/thrash/contention → **do not start**;
  record the conflict.
- Intended accelerator unavailable → **investigate**, then either fix or make the
  CPU decision explicit (with the runtime cost that implies, from G4).
