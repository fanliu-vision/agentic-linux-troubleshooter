from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.integration


def test_systemd_service_template_exists() -> None:
    path = PROJECT_ROOT / "systemd" / "agentic-monitor@.service"
    assert path.exists()

    text = path.read_text(encoding="utf-8")

    assert "Description=Agentic Linux Monitor & Auto-Recovery Agent" in text
    assert "EnvironmentFile=/etc/agentic-linux-troubleshooter/%i.env" in text
    assert "main_monitor.py" in text
    assert "--daemon" in text
    assert "Restart=always" in text
    assert "KillSignal=SIGTERM" in text


def test_systemd_env_example_exists() -> None:
    path = PROJECT_ROOT / "systemd" / "agentic-monitor.env.example"
    assert path.exists()

    text = path.read_text(encoding="utf-8")

    assert "PROJECT_ROOT=" in text
    assert "PYTHON_BIN=" in text
    assert "CONFIG_PATH=" in text
    assert "REPORT_MODE=" in text
    assert "HEARTBEAT_INTERVAL=" in text
    assert "HEALTH_CHECK_INTERVAL=" in text


def test_systemd_scripts_exist() -> None:
    install_script = PROJECT_ROOT / "scripts" / "install_systemd_service.sh"
    uninstall_script = PROJECT_ROOT / "scripts" / "uninstall_systemd_service.sh"

    assert install_script.exists()
    assert uninstall_script.exists()

    install_text = install_script.read_text(encoding="utf-8")
    uninstall_text = uninstall_script.read_text(encoding="utf-8")

    assert "systemctl enable" in install_text
    assert "systemctl restart" in install_text
    assert "systemctl stop" in uninstall_text
    assert "systemctl disable" in uninstall_text


def main() -> None:
    test_systemd_service_template_exists()
    test_systemd_env_example_exists()
    test_systemd_scripts_exist()

    print("=" * 100)
    print("STAGE 6E SYSTEMD ASSETS TEST PASSED")
    print("=" * 100)


if __name__ == "__main__":
    main()
