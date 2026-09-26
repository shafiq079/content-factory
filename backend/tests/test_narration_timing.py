"""Voice duration drives video generation and prompt edits keep existing narration."""
from pathlib import Path

from fastapi.testclient import TestClient

from app import core, providers
from app.main import app
from test_pipeline import wait_for_completion


def test_voice_first_timing_and_regeneration(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    events = []

    class FakeVoice:
        def generate(self, text, output, seconds, language, voice_id="", speed=1.0):
            events.append(("voice", 1 if "Updated" in text else 0))
            core.run("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                     "-t", "3" if "Updated" in text else "2", "-c:a", "pcm_s16le", str(output))

    class LoggedVideo:
        def generate(self, scene, output, request):
            events.append(("video", scene["duration"]))
            core.PreviewVideo().generate(scene, output, request)

    monkeypatch.setitem(providers.REGISTRY["voice"], "kokoro", providers.Adapter(FakeVoice, providers.ready))
    monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(LoggedVideo, providers.ready))
    with TestClient(app) as client:
        created = client.post("/projects", json={"topic": "Black holes", "duration": 10,
                                                 "width": 256, "height": 448, "voice_provider": "kokoro"})
        assert created.status_code == 202, created.text
        project_id = created.json()["id"]
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert events == [("voice", 0), ("video", 2.0), ("voice", 0), ("video", 2.0)]
        assert result["duration_actual"] == 4
        planned = [scene["planned_duration"] for scene in result["scenes"]]
        assert round(sum(planned), 3) == 10
        assert planned[0] != planned[1]
        assert result["scenes"][1]["start"] == 2

        response = client.post(f"/projects/{project_id}/scenes/1/regenerate", json={"visual_prompt": "New close-up action"})
        assert response.status_code == 202, response.text
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert events[-1] == ("video", 2.0)
        assert sum(kind == "voice" for kind, _ in events) == 2

        response = client.post(f"/projects/{project_id}/scenes/1/regenerate", json={"narration": "Updated narration for the first scene."})
        assert response.status_code == 202, response.text
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert events[-2:] == [("voice", 1), ("video", 3.0)]
        assert result["duration_actual"] == 5
        assert result["scenes"][1]["start"] == 3
