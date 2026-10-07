#!/usr/bin/env python3
"""G5 accelerator check: prove the framework actually SEES and USES the intended
device — not merely that a driver/toolkit is installed.

Probes PyTorch (if present), places a real tensor on the requested device, runs a
trivial op, and reports device/backend/precision. Exits:
  0  -> OK: framework placed a tensor on the intended device
  2  -> FAIL-CLOSED: intended accelerator unavailable (investigate before using CPU)
  3  -> UNKNOWN: no supported framework found to verify with

Supported --want: cuda | rocm | mps | cpu
(On ROCm/WSL, PyTorch exposes the AMD GPU through HIP, so torch.cuda.is_available()
is the correct probe for --want rocm.)

Only `cpu` is allowed to pass trivially — and only when explicitly requested, so a
CPU fallback is a decision, never an accident.
"""
from __future__ import annotations

import argparse
import sys


def check_torch(want: str) -> tuple[bool, str]:
    try:
        import torch
    except ImportError:
        return False, "pytorch not importable"

    details: list[str] = [f"torch={torch.__version__}"]

    if want in ("cuda", "rocm"):
        # ROCm builds report through the CUDA API via HIP.
        hip = getattr(torch.version, "hip", None)
        cuda = getattr(torch.version, "cuda", None)
        details.append(f"cuda={cuda} hip={hip}")
        if not torch.cuda.is_available():
            return False, "; ".join(details) + "; torch.cuda.is_available()=False"
        if want == "cuda" and hip is not None:
            return False, "; ".join(details) + "; built for ROCm/HIP, not CUDA"
        if want == "rocm" and hip is None:
            return False, "; ".join(details) + "; built for CUDA, not ROCm/HIP"
        dev = "cuda"
    elif want == "mps":
        if not (
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
        ):
            return False, "; ".join(details) + "; torch.backends.mps unavailable"
        dev = "mps"
    elif want == "cpu":
        dev = "cpu"
    else:
        return False, f"unknown device '{want}'"

    # The decisive test: place a tensor and run an op ON the device.
    try:
        x = torch.ones(8, 8, device=dev)
        y = (x @ x).sum().item()
        placed = str(x.device)
    except (RuntimeError, AssertionError) as exc:
        return False, "; ".join(details) + f"; tensor placement on {dev} failed: {exc}"

    if not placed.startswith(dev):
        return False, "; ".join(details) + f"; tensor landed on {placed}, not {dev}"

    if dev != "cpu":
        try:
            name = torch.cuda.get_device_name(0) if dev == "cuda" else dev
            details.append(f"gpu={name}")
        except (RuntimeError, AssertionError):
            pass
    details.append(f"placed={placed} op_ok={y == 8 * 8 * 8}")
    return True, "; ".join(details)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--want", required=True, choices=["cuda", "rocm", "mps", "cpu"])
    args = ap.parse_args()

    ok, detail = check_torch(args.want)
    print(f"[accelerator] want={args.want}  {detail}")

    if detail == "pytorch not importable":
        print(
            "VERDICT: UNKNOWN — no supported framework to verify with. "
            "Verify your framework sees the device by hand before training."
        )
        return 3
    if ok:
        print(f"VERDICT: OK — framework is using '{args.want}'.")
        return 0
    if args.want == "cpu":
        # Should not happen, but keep the CPU path explicit.
        print("VERDICT: FAIL-CLOSED — CPU placement itself failed.")
        return 2
    print(
        f"VERDICT: FAIL-CLOSED — intended accelerator '{args.want}' is NOT usable. "
        "Investigate (driver/toolkit/WSL-ROCm path) before falling back to CPU. "
        "A CPU fallback must be an explicit, justified decision, not accidental."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
