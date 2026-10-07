#!/usr/bin/env python3
"""G5 resource preflight: decide GO / NO-GO before a heavy run.

Snapshots CPU load, RAM/swap, GPU/VRAM, disk, and the heaviest running processes,
then checks them against the declared need. Prints a human verdict and exits:
  0  -> GO (capacity available, no blocking contention)
  2  -> NO-GO (fail-closed: insufficient resource or heavy contention risk)
  3  -> UNKNOWN (could not measure something required; treat as NO-GO manually)

Uses only the stdlib plus external CLIs that are optional (nvidia-smi, rocm-smi,
free, df). Degrades cleanly: a probe that cannot run is reported as unknown, never
silently assumed healthy. Works on Linux / WSL / macOS; partial on Windows-native.

Thresholds are explicit and documented below — no magic numbers.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys

# --- Explicit, documented thresholds (not voodoo constants) ---------------
# A heavy run should not start into a machine already saturated. These are the
# contention guards; the *capacity* guards come from the user-declared --need-*.

# If 1-min load average exceeds this multiple of the CPU count, another workload
# is already saturating the CPU and a new heavy job will thrash.
LOAD_PER_CPU_BLOCK = 1.25
# Leave this fraction of RAM as headroom beyond the declared need, so the OS and
# the existing processes are not pushed into swap by the new job.
RAM_HEADROOM_FRAC = 0.10
# Swap already in active use beyond this fraction signals existing memory
# pressure — starting another heavy job risks OOM/thrash.
SWAP_USED_BLOCK_FRAC = 0.25


def _run(cmd: list[str]) -> str | None:
    """Run a CLI, returning stdout or None if it is unavailable/fails."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False
        )
        return out.stdout if out.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def cpu_load() -> tuple[float | None, int]:
    """Return (1-min load average, cpu count). Load is None where unsupported."""
    ncpu = os.cpu_count() or 1
    try:
        return os.getloadavg()[0], ncpu  # POSIX only
    except (OSError, AttributeError):
        return None, ncpu


def mem_gb() -> tuple[float | None, float | None, float | None]:
    """Return (ram_available_gb, swap_total_gb, swap_used_gb)."""
    # Prefer /proc/meminfo (Linux/WSL).
    try:
        info: dict[str, float] = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key.strip()] = float(rest.split()[0]) / (1024 * 1024)  # kB->GB
        avail = info.get("MemAvailable")
        swap_total = info.get("SwapTotal")
        swap_free = info.get("SwapFree")
        swap_used = (
            swap_total - swap_free
            if swap_total is not None and swap_free is not None
            else None
        )
        return avail, swap_total, swap_used
    except (OSError, ValueError, IndexError):
        pass
    # Windows-native fallback: query available physical RAM via GlobalMemoryStatusEx.
    # Swap is not resolved here, so it is reported unknown (never assumed healthy).
    if sys.platform.startswith("win"):
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStatus()
            stat.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullAvailPhys / (1024**3), None, None
        except (OSError, AttributeError, ValueError):
            pass
    return None, None, None


def disk_free_gb(path: str) -> float | None:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return None


def gpu_snapshot() -> tuple[str | None, float | None, float | None]:
    """Return (backend, vram_total_gb, vram_free_gb) for the first visible GPU."""
    nv = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
    )
    if nv:
        try:
            total, free = (float(x) for x in nv.splitlines()[0].split(","))
            return "cuda", total / 1024, free / 1024  # MiB -> GiB
        except (ValueError, IndexError):
            pass
    roc = _run(["rocm-smi", "--showmeminfo", "vram", "--csv"])
    if roc:
        # rocm-smi CSV columns vary by version; parse defensively.
        nums = [float(t) for t in roc.replace(",", " ").split() if _isnum(t)]
        if len(nums) >= 2:
            total_b, used_b = max(nums[:2]), min(nums[:2])
            return "rocm", total_b / (1024**3), (total_b - used_b) / (1024**3)
        return "rocm", None, None
    return None, None, None


def heavy_processes(top: int = 5) -> list[str]:
    out = _run(["ps", "axo", "pid,comm,%cpu,%mem", "--sort=-%mem"])
    if not out:
        return []
    return out.splitlines()[1 : top + 1]


def _isnum(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--need-ram-gb", type=float, default=0.0)
    ap.add_argument("--need-vram-gb", type=float, default=0.0)
    ap.add_argument("--need-disk-gb", type=float, default=0.0)
    ap.add_argument("--disk-path", default=".")
    args = ap.parse_args()

    blocks: list[str] = []  # fail-closed reasons
    unknowns: list[str] = []

    load, ncpu = cpu_load()
    print(f"[cpu]   cores={ncpu} load1={load if load is not None else 'unknown'}")
    if load is None:
        unknowns.append("cpu load average unavailable on this OS")
    elif load > LOAD_PER_CPU_BLOCK * ncpu:
        blocks.append(
            f"CPU already saturated: load {load:.2f} > {LOAD_PER_CPU_BLOCK}×{ncpu}"
        )

    avail, swap_total, swap_used = mem_gb()
    print(
        f"[ram]   available={_fmt(avail)}GB  swap_used={_fmt(swap_used)}/"
        f"{_fmt(swap_total)}GB"
    )
    if avail is None:
        unknowns.append("RAM availability unmeasurable")
    elif args.need_ram_gb and avail < args.need_ram_gb * (1 + RAM_HEADROOM_FRAC):
        blocks.append(
            f"insufficient RAM: need {args.need_ram_gb}GB "
            f"(+{int(RAM_HEADROOM_FRAC * 100)}% headroom), available {avail:.1f}GB"
        )
    if (
        swap_total
        and swap_used is not None
        and swap_total > 0
        and swap_used / swap_total > SWAP_USED_BLOCK_FRAC
    ):
        blocks.append(
            f"memory pressure: swap {swap_used:.1f}/{swap_total:.1f}GB already in use"
        )

    backend, vram_total, vram_free = gpu_snapshot()
    print(
        f"[gpu]   backend={backend or 'none-detected'} "
        f"vram_free={_fmt(vram_free)}/{_fmt(vram_total)}GB"
    )
    if args.need_vram_gb:
        if vram_free is None:
            unknowns.append(
                "VRAM need declared but no GPU/VRAM measurable "
                "(see accelerator_check.py; do not silently fall back to CPU)"
            )
        elif vram_free < args.need_vram_gb:
            blocks.append(
                f"insufficient VRAM: need {args.need_vram_gb}GB, free {vram_free:.1f}GB"
            )

    free_disk = disk_free_gb(args.disk_path)
    print(f"[disk]  free={_fmt(free_disk)}GB on {os.path.abspath(args.disk_path)}")
    if free_disk is None:
        unknowns.append("disk free space unmeasurable")
    elif args.need_disk_gb and free_disk < args.need_disk_gb:
        blocks.append(
            f"insufficient disk: need {args.need_disk_gb}GB, free {free_disk:.1f}GB"
        )

    procs = heavy_processes()
    if procs:
        print("[procs] top memory consumers (candidate conflicts):")
        for line in procs:
            print(f"          {line.strip()}")

    print("-" * 60)
    if blocks:
        print("VERDICT: NO-GO (fail-closed). Blocking conditions:")
        for b in blocks:
            print(f"  - {b}")
        if unknowns:
            print("Also unknown:")
            for u in unknowns:
                print(f"  - {u}")
        return 2
    if unknowns:
        print("VERDICT: UNKNOWN — could not verify all gates; do NOT assume GO:")
        for u in unknowns:
            print(f"  - {u}")
        return 3
    print("VERDICT: GO — declared capacity available, no blocking contention.")
    return 0


def _fmt(x: float | None) -> str:
    return f"{x:.1f}" if isinstance(x, float) and not math.isnan(x) else "unknown"


if __name__ == "__main__":
    sys.exit(main())
