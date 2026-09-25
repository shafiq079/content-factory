"""Contract failures must be visible and recoverable on the CPU-only path."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import contracts, core, media, providers
from app.main import app
from test_pipeline import wait_for_completion


def test_missing_checkpoint_rejected_before_queuing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    config = tmp_path / "models.json"
    config.write_text(json.dumps({key: str(tmp_path / "absent.bin") for key in core.LTX25Video.KEYS}))
    monkeypatch.setenv("LTX_CONFIG", str(config))
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Ocean life", "video_provider": "ltx25"})
        assert response.status_code == 422
        assert "Missing LTX checkpoint" in response.json()["detail"]
        assert not list(tmp_path.glob("*/timeline.json"))


def test_fake_video_with_wrong_duration_fails_without_publishing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)

    class ShortVideo:
        def generate(self, scene, output, request):
            core.PreviewVideo().generate({**scene, "duration": 1}, output, request)

    monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(ShortVideo, providers.ready))
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Ocean life", "duration": 10, "width": 256, "height": 448})
        project_id = response.json()["id"]
        failed = wait_for_completion(client, project_id)
        assert failed["status"] == "failed"
        assert "Clip duration" in failed["error"]
        assert not (tmp_path / project_id / "final.mp4").exists()
        assert not (tmp_path / project_id / "clips/scene-01.mp4").exists()


def test_fake_invalid_audio_and_retry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)

    class BrokenVoice:
        def generate(self, text, output, seconds, language):
            output.write_bytes(b"not a wave file")

    original = providers.REGISTRY["voice"]["silent"]
    monkeypatch.setitem(providers.REGISTRY["voice"], "silent", providers.Adapter(BrokenVoice, providers.ready))
    with TestClient(app) as client:
        project_id = client.post("/projects", json={"topic": "Ocean life", "duration": 10, "width": 256, "height": 448}).json()["id"]
        failed = wait_for_completion(client, project_id)
        assert failed["status"] == "failed"
        assert "Cannot probe" in failed["error"]
        assert (tmp_path / project_id / "clips/scene-01.mp4").exists()
        monkeypatch.setitem(providers.REGISTRY["voice"], "silent", original)
        assert client.post(f"/projects/{project_id}/retry").status_code == 202
        recovered = wait_for_completion(client, project_id)
        assert recovered["status"] == "complete", recovered["error"]
        media.validate_final(tmp_path / project_id / "final.mp4", 256, 448, 10)
        media.validate_captions(tmp_path / project_id / "captions.srt", 10)


def test_migrates_legacy_manifest_and_refuses_future_version(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    project = core.create(core.Request(topic="Ocean life"))
    path = tmp_path / project["id"] / "timeline.json"
    legacy = json.loads(path.read_text())
    legacy.pop("schema_version")
    path.write_text(json.dumps(legacy))
    assert core.load(project["id"])["schema_version"] == contracts.SCHEMA_VERSION
    assert json.loads(path.read_text())["schema_version"] == contracts.SCHEMA_VERSION
    legacy["schema_version"] = 999
    path.write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="Unsupported timeline schema"):
        core.load(project["id"])


def test_caption_contract_rejects_out_of_order_or_late_cues(tmp_path: Path):
    path = tmp_path / "captions.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:02,000\nFirst\n\n2\n00:00:01,000 --> 00:00:11,000\nSecond\n")
    with pytest.raises(media.MediaValidationError, match="outside the timeline"):
        media.validate_captions(path, 10)
