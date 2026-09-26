"""CPU tests for editable background music and timeline SFX."""
from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import audio, contracts, core, media, providers
from app.main import app
from test_pipeline import wait_for_completion


def wav_bytes(seconds: float = 1.0, frequency: float = 440.0, rate: int = 24000) -> bytes:
    frames = int(seconds * rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        samples = bytearray()
        for index in range(frames):
            value = int(7000 * math.sin(2 * math.pi * frequency * index / rate))
            samples.extend(struct.pack("<h", value))
        output.writeframes(bytes(samples))
    return buffer.getvalue()


def make_base_video(path: Path, seconds: float = 3.0) -> None:
    core.run(
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=256x448:r=24:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-t", str(seconds), str(path),
    )


def test_master_audio_mixes_looped_music_and_timeline_sfx(tmp_path: Path):
    raw = tmp_path / "raw.mp4"
    mixed = tmp_path / "mixed.mp4"
    make_base_video(raw, 3)

    project = tmp_path / "project"
    (project / "audio/music").mkdir(parents=True)
    (project / "audio/sfx").mkdir(parents=True)
    music_path = project / "audio/music/music.wav"
    sfx_path = project / "audio/sfx/hit.wav"
    music_path.write_bytes(wav_bytes(0.7, 330))
    sfx_path.write_bytes(wav_bytes(0.4, 880))

    manifest = {
        "music": {
            "provider": "uploaded", "asset": "audio/music/music.wav", "enabled": True,
            "volume": 0.2, "loop": True, "fade_in": 0.1, "fade_out": 0.3,
        },
        "sfx": [{
            "id": "a" * 32, "provider": "uploaded", "asset": "audio/sfx/hit.wav",
            "enabled": True, "start": 1.1, "duration": 0.4, "volume": 0.6,
            "fade_in": 0.0, "fade_out": 0.1,
        }],
    }
    assert audio.mix_master_audio(raw, mixed, project, manifest, 3.0)
    result = media.validate_final(mixed, 256, 448, 3.0)
    assert result.duration == pytest.approx(3.0, abs=0.2)


def test_zero_volume_music_short_circuits_without_loading_asset(tmp_path: Path):
    raw = tmp_path / "raw.mp4"
    mixed = tmp_path / "mixed.mp4"
    make_base_video(raw, 1.5)
    manifest = {
        "music": {
            "provider": "uploaded",
            "asset": "audio/music/does-not-exist.wav",
            "enabled": True,
            "volume": 0.0,
            "loop": True,
            "fade_in": 0.5,
            "fade_out": 1.0,
        },
        "sfx": [],
    }
    assert audio.mix_master_audio(raw, mixed, tmp_path, manifest, 1.5) is False
    assert not mixed.exists()


def test_music_and_sfx_api_rerenders_without_video_generation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    video_calls = []

    class LoggedVideo:
        def generate(self, scene, output, request):
            video_calls.append(scene["id"])
            core.PreviewVideo().generate(scene, output, request)

    monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(LoggedVideo, providers.ready))

    with TestClient(app) as client:
        created = client.post("/projects", json={
            "topic": "Ocean ambience",
            "duration": 10,
            "width": 256,
            "height": 448,
        })
        assert created.status_code == 202, created.text
        project_id = created.json()["id"]
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        initial_video_calls = list(video_calls)
        revision = project["revision"]

        music_response = client.post(
            f"/projects/{project_id}/music",
            files={"file": ("bed.wav", wav_bytes(0.8, 330), "audio/wav")},
            data={"volume": "0.2", "loop": "true", "fade_in": "0.1", "fade_out": "0.5"},
        )
        assert music_response.status_code == 202, music_response.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert project["revision"] == revision + 1
        assert project["music"]["provider"] == "uploaded"
        assert project["music"]["volume"] == pytest.approx(0.2)
        assert video_calls == initial_video_calls
        music_asset = tmp_path / project_id / project["music"]["asset"]
        assert music_asset.is_file()

        sfx_response = client.post(
            f"/projects/{project_id}/sfx",
            files={"file": ("hit.wav", wav_bytes(0.35, 990), "audio/wav")},
            data={"start": "2.0", "volume": "0.65", "fade_in": "0", "fade_out": "0.1"},
        )
        assert sfx_response.status_code == 202, sfx_response.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert len(project["sfx"]) == 1
        assert project["sfx"][0]["start"] == pytest.approx(2.0)
        assert project["sfx"][0]["duration"] == pytest.approx(0.35, abs=0.05)
        assert video_calls == initial_video_calls
        media.validate_final(tmp_path / project_id / "final.mp4", 256, 448, 10)


def test_invalid_audio_upload_is_rejected_before_queue(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        created = client.post("/projects", json={
            "topic": "Upload validation",
            "duration": 10,
            "width": 256,
            "height": 448,
        })
        project_id = created.json()["id"]
        complete = wait_for_completion(client, project_id)
        assert complete["status"] == "complete"

        response = client.post(
            f"/projects/{project_id}/music",
            files={"file": ("fake.mp3", b"not an audio file", "audio/mpeg")},
        )
        assert response.status_code == 422
        current = client.get(f"/projects/{project_id}").json()
        assert current["status"] == "complete"
        assert current["music"] is None


def test_schema_v2_migrates_to_editable_audio_tracks():
    legacy = {
        "schema_version": 2,
        "id": "b" * 32,
        "request": {},
        "status": "complete",
        "stage": "complete",
        "scenes": [],
        "assets": {},
        "error": None,
        "revision": 0,
        "caption_style": "classic",
    }
    migrated = contracts.migrate_timeline(legacy)
    assert migrated["schema_version"] == contracts.SCHEMA_VERSION
    assert migrated["music"] is None
    assert migrated["sfx"] == []
