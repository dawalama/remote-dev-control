from pathlib import Path

from remote_dev_ctrl.server.evidence_providers import EntireEvidenceProvider, NativeEvidenceProvider, enable_entire


def test_native_evidence_is_always_ready():
    result = NativeEvidenceProvider().inspect(None)
    assert result["ready"] is True
    assert "lineage" in result["capabilities"]


def test_entire_reports_project_readiness(monkeypatch, tmp_path: Path):
    class Result:
        returncode = 0
        stdout = '{"enabled": true}'
        stderr = ""

    monkeypatch.setattr("remote_dev_ctrl.server.evidence_providers.shutil.which", lambda name: "/usr/local/bin/entire")
    monkeypatch.setattr("remote_dev_ctrl.server.evidence_providers.subprocess.run", lambda *args, **kwargs: Result())
    result = EntireEvidenceProvider().inspect(tmp_path)
    assert result["ready"] is True
    assert result["status"] == "ready"


def test_enable_entire_is_noninteractive_and_local_first(monkeypatch, tmp_path: Path):
    captured = {}

    class Result:
        returncode = 0
        stdout = "enabled"
        stderr = ""

    monkeypatch.setattr("remote_dev_ctrl.server.evidence_providers.shutil.which", lambda name: "/usr/local/bin/entire")
    def run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return Result()
    monkeypatch.setattr("remote_dev_ctrl.server.evidence_providers.subprocess.run", run)
    result = enable_entire(tmp_path, "claude")
    assert result["agent"] == "claude-code"
    assert "--no-init-repo" in captured["command"]
    assert "--skip-push-sessions" in captured["command"]
    assert "--telemetry=false" in captured["command"]
