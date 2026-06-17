from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import traceback
from pathlib import Path


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def log(msg: str) -> None:
    print(msg, flush=True)


def err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def simulate_python_env_warning(config: dict) -> None:
    if not config.get("simulate_python_env_mismatch", False):
        return

    err("[env] CONDA_PREFIX=/opt/conda/envs/order-service")
    err("[env] VIRTUAL_ENV=<not set>")
    err("[env] python=/usr/bin/python3")
    err("[env] pip=/opt/conda/envs/order-service/bin/pip")
    err("[warning] Python interpreter and pip path do not belong to the same environment.")
    err("Traceback (most recent call last):")
    err('  File "/srv/order-service/plugins/internal_risk_sdk.py", line 7, in <module>')
    err("    import acme_internal_sdk")
    err("ModuleNotFoundError: No module named 'acme_internal_sdk'")
    err("[fallback] internal risk SDK unavailable, continue with local rule engine.")

    if config.get("fail_on_python_env", False):
        raise ModuleNotFoundError("No module named 'acme_internal_sdk'")


def simulate_cache_warning(config: dict) -> None:
    if not config.get("simulate_disk_full", False):
        return

    cache_dir = config.get("cache_dir", "/tmp/acme_order_cache")

    err(f"[cache] preparing order feature cache at {cache_dir}")
    err(f"[cache] WARNING: failed to write cache file {cache_dir}/features_0001.bin")
    err(f"OSError: [Errno 28] No space left on device: '{cache_dir}/features_0001.bin'")
    err("[cache] fallback: continue with in-memory feature cache")


def load_orders(input_file: Path) -> list[dict]:
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    orders = []
    with input_file.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                orders.append(json.loads(line))

    return orders


def process_orders(orders: list[dict]) -> dict:
    high_risk = [o for o in orders if float(o.get("risk_score", 0)) >= 0.8]
    total_amount = sum(float(o.get("amount", 0)) for o in orders)

    return {
        "total_orders": len(orders),
        "high_risk_orders": len(high_risk),
        "total_amount": round(total_amount, 2),
    }


def start_metrics_exporter(config: dict) -> None:
    host = config.get("metrics_host", "127.0.0.1")
    port = int(config.get("metrics_port", 9100))

    log(f"[metrics] starting metrics exporter on {host}:{port}")

    conflict_socket = None

    # 为了稳定复现企业服务端口冲突，这里在 9100 上模拟已有进程占用。
    if config.get("simulate_port_conflict", False) and port == 9100:
        conflict_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conflict_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conflict_socket.bind((host, port))
        conflict_socket.listen(1)
        err(f"[metrics] simulated existing process is already listening on {host}:{port}")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        server_socket.bind((host, port))
        server_socket.listen(1)
        log(f"[metrics] exporter started successfully on {host}:{port}")
    except OSError as exc:
        err("Traceback (most recent call last):")
        err('  File "/srv/order-service/run_service.py", line 132, in start_metrics_exporter')
        err(f"    server_socket.bind(({host!r}, {port}))")
        err(f"OSError: [Errno {exc.errno}] {exc.strerror}")
        err("[summary]")
        err("primary_failure=Address already in use")
        err(
            "secondary_issues=python interpreter mismatch, missing acme_internal_sdk, "
            "cache no space left, metrics port conflict"
        )
        raise
    finally:
        server_socket.close()
        if conflict_socket is not None:
            conflict_socket.close()


def write_result(config: dict, metrics: dict) -> None:
    output_dir = Path(config.get("output_dir", "outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)

    result_path = output_dir / "service_result.json"
    result_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    success_marker = output_dir / "service_started.ok"
    success_marker.write_text(
        "enterprise order monitoring service started successfully\n"
        f"metrics_port={config.get('metrics_port')}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)

    log(f"[service] starting {config.get('service_name')}")
    log(f"[service] environment={config.get('environment')}")
    log(f"[service] config={config_path}")

    try:
        simulate_python_env_warning(config)
        simulate_cache_warning(config)

        input_file = Path(config.get("input_file", "data/orders.jsonl"))
        log(f"[data] loading orders from {input_file}")
        orders = load_orders(input_file)

        log(f"[data] loaded orders={len(orders)}")
        metrics = process_orders(orders)
        log(f"[risk] processed metrics={metrics}")

        time.sleep(0.2)

        start_metrics_exporter(config)

        write_result(config, metrics)

        log("[service] enterprise order monitoring service started successfully")
        log("[service] health=OK")
        return 0

    except Exception:
        err("[service] startup failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())