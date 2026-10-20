import importlib.util
import json


def test_persistent_failure_then_recovery(tmp_path):
    spec = importlib.util.spec_from_file_location("health", "deploy/capture-health.py")
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    path = tmp_path / "health.json"
    sent = []
    notify = lambda: sent.append(True)
    assert health.update(path, False, notify) == "ok"
    assert health.update(path, False, notify) == "ok"
    assert health.update(path, False, notify) == "alert"
    assert health.update(path, False, notify) == "ok"
    health.update(path, True, notify)
    health.update(path, True, notify)
    assert sent == [True]
    assert json.loads(path.read_text())["failures"] == 0
    assert path.stat().st_mode & 0o777 == 0o600


def test_transient_failure_no_alert_and_email_retry(tmp_path):
    spec = importlib.util.spec_from_file_location("health", "deploy/capture-health.py")
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    path = tmp_path / "health.json"
    notify = lambda: None
    health.update(path, False, notify)
    health.update(path, True, notify)
    assert not json.loads(path.read_text()).get("alerted")
    for _ in range(3):
        health.update(path, False, notify)
    def unavailable():
        raise TimeoutError()
    health.update(path, True, unavailable)
    assert json.loads(path.read_text())["recovery_pending"]
    health.update(path, True, notify)
    assert "recovery_pending" not in json.loads(path.read_text())
