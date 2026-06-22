from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fixers.remote_safe_config_editor import RemoteSafeConfigEditor


def test_remote_editor_script_allows_safe_enum_downgrade(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    config_path.write_text(
        json.dumps({"cache": {"backend": "redis"}}, indent=2),
        encoding="utf-8",
    )

    result = run_remote_editor_script(
        {
            "op": "update_json_field",
            "remote_project_dir": str(project_dir),
            "relative_config_path": "config.json",
            "field_path": "cache.backend",
            "new_value": "memory",
            "fix_id": "fix-test-enum-1",
            "semantic_rule": "safe_enum_downgrade",
        }
    )

    assert result["success"] is True
    assert result["semantic_status"] == "actionable"
    assert result["semantic_reason"] == "safe_enum_downgrade_target_allowlisted"
    assert Path(result["backup_path"]).exists()
    assert Path(result["diff_path"]).exists()
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["cache"]["backend"] == "memory"


def test_remote_editor_script_rejects_unsafe_enum_target(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "config.json"
    original = {"cache": {"backend": "memory"}}
    config_path.write_text(json.dumps(original, indent=2), encoding="utf-8")

    result = run_remote_editor_script(
        {
            "op": "update_json_field",
            "remote_project_dir": str(project_dir),
            "relative_config_path": "config.json",
            "field_path": "cache.backend",
            "new_value": "remote",
            "fix_id": "fix-test-enum-1",
            "semantic_rule": "safe_enum_downgrade",
        }
    )

    assert result["success"] is False
    assert result["semantic_status"] == "unsafe"
    assert result["semantic_reason"] == "safe_enum_target_not_allowlisted"
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
    assert not (project_dir / ".agent_backups").exists()


def run_remote_editor_script(payload: dict[str, Any]) -> dict[str, Any]:
    payload_b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            RemoteSafeConfigEditor._remote_editor_script(),
            payload_b64,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)
