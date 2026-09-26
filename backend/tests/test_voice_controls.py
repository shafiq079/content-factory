"""Project-level reusable Kokoro voice controls without GPU work."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core, providers
from app.main import app
from test_pipeline import wait_for_completion


def test_project_revoice_reuses_existing_video_and_persists_voice(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    voice_calls = []
    video_calls = []

    class FakeVoice:
        def generate(self, text, output, seconds, language, voice_id="", speed=1.0):
            voice_calls.append((voice_id, speed, text))
            duration = 2 if speed >= 1 else 3
            core.run(
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                "-t", str(duration), "-c:a", "pcm_s16le", str(output),
            )

    class LoggedVideo:
        def generate(self, scene, output, request):
            video_calls.append(scene["id"])
            core.PreviewVideo().generate(scene, output, request)

    monkeypatch.setitem(providers.REGISTRY["voice"], "kokoro", providers.Adapter(FakeVoice, providers.ready))
    monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(LoggedVideo, providers.ready))

    with TestClient(app) as client:
        created = client.post("/projects", json={
            "topic": "Black holes",
            "duration": 10,
            "width": 256,
            "height": 448,
            "voice_provider": "kokoro",
            "voice_id": "af_heart",
            "voice_speed": 1.0,
        })
        assert created.status_code == 202, created.text
        project_id = created.json()["id"]
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        initial_video_calls = list(video_calls)
        initial_revision = result["revision"]
        assert result["request"]["voice_id"] == "af_heart"
        assert result["request"]["voice_speed"] == 1.0
        assert result["duration_actual"] == 4

        changed = client.post(f"/projects/{project_id}/voice", json={
            "voice_id": "af_bella",
            "voice_speed": 0.8,
        })
        assert changed.status_code == 202, changed.text
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert result["request"]["voice_id"] == "af_bella"
        assert result["request"]["voice_speed"] == 0.8
        assert result["revision"] == initial_revision + 1
        assert result["duration_actual"] == 6
        assert video_calls == initial_video_calls
        assert [call[:2] for call in voice_calls[-2:]] == [("af_bella", 0.8), ("af_bella", 0.8)]
        assert result["scenes"][1]["start"] == 3


def test_voice_settings_validation():
    assert core.resolve_kokoro_voice("English", "af_heart") == "af_heart"
    assert core.resolve_kokoro_voice("English", "af_heart,am_adam") == "af_heart,am_adam"

    with pytest.raises(ValueError, match="match language code"):
        core.resolve_kokoro_voice("English", "bf_emma")
    with pytest.raises(ValueError):
        core.Request(topic="Voice test", voice_speed=0.2)
    with pytest.raises(ValueError):
        core.Request(topic="Voice test", voice_id="../voice.pt")


def test_default_kokoro_voice_is_persisted(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("KOKORO_VOICE", "af_heart")
    request = core.Request(topic="Voice test", voice_provider="kokoro")
    project = core.create(request)
    assert project["request"]["voice_id"] == "af_heart"
