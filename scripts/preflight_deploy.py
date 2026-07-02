#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
from pathlib import Path


def main() -> int:
    args = parse_args()
    checks = [
        check_path("project_root", Path(args.project_root), must_exist=True, must_be_dir=True),
        check_path("python_bin", Path(args.python_bin), must_exist=True, executable=True),
        check_path("config", Path(args.config), must_exist=True),
        check_writable_dir("state_dir", Path(args.state_dir)),
        check_writable_dir("output_root", Path(args.output_root)),
        check_port(args.host, int(args.port)),
        check_systemd(),
        check_tokens(args),
    ]
    ok = all(item[0] for item in checks)
    for passed, name, message in checks:
        status = "OK" if passed else "FAIL"
        print(f"[{status}] {name}: {message}")
    return 0 if ok else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight checks for venv + systemd deployment.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--python-bin", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-env", default="AGENTIC_TRACE_UI_TOKEN")
    parser.add_argument("--viewer-token-env", default="AGENTIC_TRACE_UI_VIEWER_TOKEN")
    parser.add_argument("--operator-token-env", default="AGENTIC_TRACE_UI_OPERATOR_TOKEN")
    parser.add_argument("--approver-token-env", default="AGENTIC_TRACE_UI_APPROVER_TOKEN")
    parser.add_argument("--admin-token-env", default="AGENTIC_TRACE_UI_ADMIN_TOKEN")
    parser.add_argument("--skip-token-check", action="store_true")
    return parser.parse_args()


def check_path(
    name: str,
    path: Path,
    *,
    must_exist: bool = False,
    must_be_dir: bool = False,
    executable: bool = False,
) -> tuple[bool, str, str]:
    if must_exist and not path.exists():
        return False, name, f"missing: {path}"
    if must_be_dir and not path.is_dir():
        return False, name, f"not a directory: {path}"
    if executable and not os.access(path, os.X_OK):
        return False, name, f"not executable: {path}"
    return True, name, str(path)


def check_writable_dir(name: str, path: Path) -> tuple[bool, str, str]:
    target = path if path.exists() else path.parent
    if not target.exists():
        return False, name, f"parent missing: {target}"
    if not os.access(target, os.W_OK):
        return False, name, f"not writable by current user: {target}"
    return True, name, f"{path} (parent writable)"


def check_port(host: str, port: int) -> tuple[bool, str, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        in_use = sock.connect_ex((host, port)) == 0
    if in_use:
        return False, "trace_ui_port", f"{host}:{port} is already in use"
    return True, "trace_ui_port", f"{host}:{port} is available"


def check_systemd() -> tuple[bool, str, str]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False, "systemd", "systemctl not found"
    return True, "systemd", systemctl


def check_tokens(args: argparse.Namespace) -> tuple[bool, str, str]:
    if args.skip_token_check:
        return True, "auth_tokens", "skipped"
    env_names = [
        args.token_env,
        args.viewer_token_env,
        args.operator_token_env,
        args.approver_token_env,
        args.admin_token_env,
    ]
    present = [name for name in env_names if os.environ.get(name)]
    if not present:
        return False, "auth_tokens", "set admin token or at least one role token"
    return True, "auth_tokens", ", ".join(present)


if __name__ == "__main__":
    sys.exit(main())
