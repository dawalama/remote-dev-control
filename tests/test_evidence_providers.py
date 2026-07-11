from pathlib import Path

from remote_dev_ctrl.server.evidence_providers import EntireEvidenceProvider, NativeEvidenceProvider


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
