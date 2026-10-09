"""Exercise the readiness shell without Docker, IBKR, sleeps or network."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "deploy" / "ensure-gateway.sh"


def run_probe(tmp_path, *, failures, restart_fails=False, host="127.0.0.1"):
    binary = tmp_path / ".venv/bin"
    binary.mkdir(parents=True)
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    probe = binary / "sillage"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        'n=0; [[ ! -f count ]] || n=$(<count); n=$((n+1)); echo "$n" >count\n'
        f'[[ "$n" -gt {failures} ]]\n'
    )
    probe.chmod(0o700)
    (deploy / "restart-gateway.sh").write_text(
        "echo restart >>restarts\n" + ("exit 1\n" if restart_fails else "exit 0\n")
    )
    env = {
        **os.environ,
        "SILLAGE_ROOT": str(tmp_path),
        "SILLAGE_IB_HOST": host,
        "SILLAGE_IB_PORT": "4002",
        "SILLAGE_IB_CLIENT_ID": "91",
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, timeout=10)


def test_healthy_gateway_is_not_restarted(tmp_path):
    assert run_probe(tmp_path, failures=0).returncode == 0
    assert not (tmp_path / "restarts").exists()


def test_transient_failure_retries_without_restart(tmp_path):
    assert run_probe(tmp_path, failures=1).returncode == 0
    assert not (tmp_path / "restarts").exists()


def test_unhealthy_gateway_restarts_once_then_checks_again(tmp_path):
    assert run_probe(tmp_path, failures=2).returncode == 0
    assert (tmp_path / "restarts").read_text() == "restart\n"
    assert (tmp_path / "count").read_text().strip() == "3"


def test_failed_restart_prevents_cycle(tmp_path):
    assert run_probe(tmp_path, failures=2, restart_fails=True).returncode != 0
    assert (tmp_path / "count").read_text().strip() == "2"


def test_still_unavailable_after_restart_fails_closed(tmp_path):
    assert run_probe(tmp_path, failures=3).returncode != 0
    assert (tmp_path / "restarts").read_text() == "restart\n"


def test_automatic_restart_refuses_remote_endpoint(tmp_path):
    assert run_probe(tmp_path, failures=0, host="other-server").returncode != 0
    assert not (tmp_path / "count").exists()
