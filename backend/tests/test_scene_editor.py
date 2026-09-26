"""CPU scene edits: source preservation, timing and GPU isolation."""
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from app import contracts, core, providers
from app.main import app
from test_pipeline import wait_for_completion


def test_replace_clip_and_edit_narration_without_video_model(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Scene editor", "duration": 10, "width": 256, "height": 448})
        project_id = response.json()["id"]
        initial = wait_for_completion(client, project_id)
        assert initial["status"] == "complete", initial["error"]
        folder = tmp_path / project_id
        first = initial["scenes"][0]
        original = folder / first["clip"]

        def no_video(*args, **kwargs):
            raise AssertionError("Video generator called during an editor-only action")
        monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(no_video, no_video))

        unsafe = client.post(f"/projects/{project_id}/scenes/1/clip",
                             files={"file": ("../not-video.mp4", b"bad input", "video/mp4")})
        assert unsafe.status_code == 422
        assert client.get(f"/projects/{project_id}").json()["scenes"][0]["clip"] == first["clip"]

        source = (folder / initial["scenes"][1]["clip"]).read_bytes()
        changed = client.post(f"/projects/{project_id}/scenes/1/clip",
                              files={"file": ("new.mp4", source, "video/mp4")})
        assert changed.status_code == 202, changed.text
        edited = wait_for_completion(client, project_id)
        assert edited["status"] == "complete", edited["error"]
        assert edited["scenes"][0]["clip_origin"] == "uploaded"
        assert edited["scenes"][0]["original_clip"] == first["clip"]
        assert original.is_file()
        assert edited["scenes"][0]["clip"] != first["clip"]

        changed = client.post(f"/projects/{project_id}/scenes/1/narration",
                              json={"narration": "A new explanation for this shot."})
        assert changed.status_code == 202, changed.text
        voiced = wait_for_completion(client, project_id)
        assert voiced["status"] == "complete", voiced["error"]
        assert voiced["scenes"][0]["clip"] == edited["scenes"][0]["clip"]
        assert voiced["scenes"][0]["voice"] != first["voice"]
        assert (folder / first["voice"]).is_file()
        assert voiced["hook"] == "A new explanation for this shot."
        assert "new explanation" in (folder / "captions.srt").read_text()
        assert voiced["revision"] == initial["revision"] + 2


def test_uploaded_narration_changes_timeline_and_rejects_native_without_audio(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Audio editing", "duration": 10, "width": 256, "height": 448})
        project_id = response.json()["id"]
        initial = wait_for_completion(client, project_id)
        assert initial["status"] == "complete", initial["error"]
        folder = tmp_path / project_id
        wav = tmp_path / "short.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:a", "pcm_s16le", str(wav)], check=True)

        changed = client.post(f"/projects/{project_id}/scenes/1/narration/upload",
                              files={"file": ("short.wav", wav.read_bytes(), "audio/wav")},
                              data={"narration": "The replacement voice line."})
        assert changed.status_code == 202, changed.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert project["scenes"][0]["voice_origin"] == "uploaded"
        assert abs(project["scenes"][0]["duration"] - 2) < 0.1
        assert project["scenes"][1]["start"] == project["scenes"][0]["duration"]
        assert project["duration_actual"] == round(sum(s["duration"] for s in project["scenes"]), 3)
        assert "replacement voice" in (folder / "captions.srt").read_text()
        assert (folder / initial["scenes"][0]["voice"]).is_file()

        native = client.post(f"/projects/{project_id}/scenes/1/audio-mode", json={"audio_mode": "native"})
        assert native.status_code == 422
        assert "audio track" in native.text


def test_schema_v4_migrates_asset_origin():
    old = {"schema_version": 4, "id": "b" * 32, "request": {}, "status": "complete",
           "stage": "complete", "scenes": [{"id": 1, "duration": 5, "narration": "One shot",
           "visual_prompt": "A landscape", "clip": "clips/scene-01.mp4", "voice": "voice/scene-01.wav"}],
           "assets": {}, "error": None, "revision": 0}
    upgraded = contracts.migrate_timeline(old)
    assert upgraded["schema_version"] == 5
    assert upgraded["scenes"][0]["original_clip"] == "clips/scene-01.mp4"
    assert upgraded["scenes"][0]["original_voice"] == "voice/scene-01.wav"


def test_native_mode_requires_audio_on_replacement(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Native replacement", "duration": 10,
                                                   "width": 256, "height": 448})
        project_id = response.json()["id"]
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        clip = tmp_path / "native.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=256x448:d=3:r=24", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", str(clip)], check=True)
        response = client.post(f"/projects/{project_id}/scenes/1/clip",
                               files={"file": ("native.mp4", clip.read_bytes(), "video/mp4")})
        assert response.status_code == 202, response.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        response = client.post(f"/projects/{project_id}/scenes/1/audio-mode", json={"audio_mode": "native"})
        assert response.status_code == 202, response.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert project["scenes"][0]["audio_mode"] == "native"
        assert client.post(f"/projects/{project_id}/scenes/1/narration",
                           json={"narration": "This cannot rewrite native dialogue"}).status_code == 422
        silent_clip = (tmp_path / project_id / project["scenes"][1]["clip"]).read_bytes()
        response = client.post(f"/projects/{project_id}/scenes/1/clip",
                               files={"file": ("silent.mp4", silent_clip, "video/mp4")})
        assert response.status_code == 422
        assert client.get(f"/projects/{project_id}").json()["scenes"][0]["clip"] == project["scenes"][0]["clip"]
