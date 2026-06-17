from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def print_header(config: dict) -> None:
    print("[train] Starting simulated SR training project")
    print(f"[train] model={config.get('model')}")
    print(f"[train] device={config.get('device')}")
    print(f"[train] precision={config.get('precision')}")
    print(f"[train] batch_size={config.get('batch_size')}")
    print(f"[train] gradient_checkpointing={config.get('gradient_checkpointing')}")
    print(f"[train] cache_dir={config.get('cache_dir')}")
    print("[train] Loading dataset from /data/DIV2K")
    print("[train] Building model...")
    print("[train] Warmup step...")


def simulate_hip_oom(config: dict) -> int:
    batch_size = int(config.get("batch_size", 1))
    max_safe_batch_size = int(config.get("max_safe_batch_size", 8))

    if batch_size <= max_safe_batch_size:
        return 0

    print("[warning] GPU/DCU memory usage is increasing quickly.", file=sys.stderr)
    print("[hy-smi snapshot]", file=sys.stderr)
    print("card 0: Hygon DCU", file=sys.stderr)
    print("vram Total Memory (MiB): 65520", file=sys.stderr)
    print("vram Used Memory (MiB): 64112", file=sys.stderr)
    print("vram Free Memory (MiB): 1408", file=sys.stderr)
    print("HCU use (%): 97", file=sys.stderr)
    print("", file=sys.stderr)

    print("Traceback (most recent call last):", file=sys.stderr)
    print('  File "/home/lf/projects/sr-train/train.py", line 284, in <module>', file=sys.stderr)
    print("    main()", file=sys.stderr)
    print('  File "/home/lf/projects/sr-train/train.py", line 251, in main', file=sys.stderr)
    print("    loss = trainer.train_one_step(batch)", file=sys.stderr)
    print('  File "/home/lf/projects/sr-train/trainer.py", line 109, in train_one_step', file=sys.stderr)
    print("    sr = model(lr)", file=sys.stderr)
    print("torch.OutOfMemoryError: HIP out of memory. Tried to allocate 2.00 GiB.", file=sys.stderr)
    print("GPU 0 has a total capacity of 63.98 GiB of which 1.37 GiB is free.", file=sys.stderr)
    print(
        "If reserved but unallocated memory is large try setting "
        "PYTORCH_HIP_ALLOC_CONF=expandable_segments:True to avoid fragmentation.",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print("[slurmstepd] error: Detected 1 oom-kill event(s) in StepId=582913.batch.", file=sys.stderr)
    print("[slurmstepd] error: Job 582913 exceeded memory or accelerator memory constraints.", file=sys.stderr)
    print("[summary]", file=sys.stderr)
    print("primary_failure=HIP out of memory", file=sys.stderr)
    print("secondary_issues=large batch size, fp32 precision, no gradient checkpointing", file=sys.stderr)

    return 1


def write_success_marker(config: dict) -> None:
    output_dir = Path(config.get("output_dir", "outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)

    marker = output_dir / "train_success.log"
    marker.write_text(
        "Simulated training finished successfully.\n"
        f"batch_size={config.get('batch_size')}\n"
        f"precision={config.get('precision')}\n"
        f"gradient_checkpointing={config.get('gradient_checkpointing')}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)

    print_header(config)

    for step in range(1, 4):
        print(f"[train] epoch=1 step={step} loss={0.2 / step:.4f}")
        time.sleep(0.1)

    oom_code = simulate_hip_oom(config)
    if oom_code != 0:
        return oom_code

    print("[train] Training completed successfully.")
    write_success_marker(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())