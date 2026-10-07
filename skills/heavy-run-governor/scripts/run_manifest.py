#!/usr/bin/env python3
"""Emit the durable run manifest required by the heavy-run-governor skill (G6).

Auto-fills what it can (git commit, host, device/backend, cpu/ram/gpu, start time)
and leaves declared-but-unknown fields as null so the gaps are VISIBLE rather than
silently absent. Hash helpers are provided so data/representation/model-input
contracts can be pinned for reproduction (G8).

Exit:
  0 -> manifest written
  2 -> could not write the output file (the run is not auditable; treat as blocking)

No network, stdlib only. See reference/run-manifest.md for field meanings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time


def _cli(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        return out.stdout.strip() if out.returncode == 0 else None
    except (subprocess.SubprocessError, OSError):
        return None


def git_commit() -> str | None:
    return _cli(["git", "rev-parse", "HEAD"])


def hash_path(path: str) -> str | None:
    """SHA-256 of a file, or of a directory's sorted file contents."""
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    try:
        if os.path.isfile(path):
            _feed(h, path)
        else:
            for root, _dirs, files in os.walk(path):
                for name in sorted(files):
                    fp = os.path.join(root, name)
                    h.update(os.path.relpath(fp, path).encode("utf-8"))
                    _feed(h, fp)
        return h.hexdigest()
    except OSError:
        return None


def _feed(h: "hashlib._Hash", filepath: str) -> None:
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)


def device_snapshot() -> dict[str, object]:
    snap: dict[str, object] = {
        "host": platform.node(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "device": None,
        "backend": None,
        "gpu": None,
        "vram_total_gb": None,
    }
    try:
        import torch

        snap["torch"] = torch.__version__
        if torch.cuda.is_available():
            snap["device"] = "cuda"
            snap["backend"] = "rocm" if getattr(torch.version, "hip", None) else "cuda"
            try:
                snap["gpu"] = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                snap["vram_total_gb"] = round(props.total_memory / (1024**3), 2)
            except (RuntimeError, AssertionError):
                pass
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            snap["device"] = "mps"
            snap["backend"] = "mps"
        else:
            snap["device"] = "cpu"
            snap["backend"] = "cpu"
    except ImportError:
        pass
    return snap


def build_manifest(args: argparse.Namespace) -> dict[str, object]:
    return {
        "run_id": args.run_id,
        "git_commit": git_commit(),
        "data_hash": hash_path(args.data) if args.data else None,
        "representation_hash": hash_path(args.representation) if args.representation else None,
        "model_input_contract_hash": hash_path(args.model_input) if args.model_input else None,
        "environment": device_snapshot(),
        "precision": args.precision,
        "seed": args.seed,
        "model_config": _load_json(args.model_config),
        "optimizer_config": _load_json(args.optimizer_config),
        "stopping_config": _load_json(args.stopping_config),
        "splits": {"train_id": args.train_id, "val_id": args.val_id, "test_id": args.test_id},
        "holdout_looks": [],  # append one entry per protected-test look (G7)
        "timing": {"start_epoch": int(time.time()), "end_epoch": None, "wall_time_s": None},
        "peak_memory": None,
        "throughput": None,
        "checkpoint_paths": [],
        "best_checkpoint": None,
        "final_artifact_hashes": {},
        "validator_results": {"g1": None, "g2": None, "g3": None},
        "final_scientific_status": None,  # positive | negative | inconclusive (per G0)
    }


def _load_json(path: str | None) -> object:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default="manifest.json")
    ap.add_argument("--data", help="path to the dataset (hashed)")
    ap.add_argument("--representation", help="path to the representation (hashed)")
    ap.add_argument("--model-input", help="path to the model-input contract (hashed)")
    ap.add_argument("--precision")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--model-config")
    ap.add_argument("--optimizer-config")
    ap.add_argument("--stopping-config")
    ap.add_argument("--train-id")
    ap.add_argument("--val-id")
    ap.add_argument("--test-id")
    args = ap.parse_args()

    manifest = build_manifest(args)
    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
    except OSError as exc:
        print(f"VERDICT: BLOCKING — could not write manifest to {args.out}: {exc}")
        return 2

    missing = [k for k in ("git_commit",) if manifest[k] is None]
    print(f"[manifest] wrote {args.out} for run_id={args.run_id}")
    if missing:
        print(f"[manifest] note: unresolved auto-fields: {', '.join(missing)}")
    print(
        "Remember to fill at run end: timing.end/wall_time, peak_memory, throughput, "
        "checkpoint_paths, best_checkpoint, final_artifact_hashes, validator_results, "
        "final_scientific_status — and append every holdout look."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
