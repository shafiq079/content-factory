"""End-to-end test of the honest CPU preview, including scene regeneration."""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import core
from app.main import app


def wait_for_completion(client: TestClient, project_id: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        project = client.get(f"/projects/{project_id}").json()
        if project["status"] in ("complete", "failed"):
            return project
        time.sleep(0.2)
    raise AssertionError("Preview did not complete in 90 seconds")


def test_preview_and_regenerate(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Black holes", "duration": 10, "width": 256, "height": 448})
        assert response.status_code == 202
        project_id = response.json()["id"]
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert len(result["scenes"]) == 2
        assert result["scenes"][0]["start"] == 0
        assert result["scenes"][1]["start"] == result["scenes"][0]["duration"]
        assert client.get(f"/projects/{project_id}/assets/final.mp4").status_code == 200
        assert client.get(f"/projects/{project_id}/assets/timeline.json").status_code == 200
        assert client.get(f"/projects/{project_id}/assets/../../app/core.py").status_code == 404
        assert (tmp_path / project_id / "captions.srt").is_file()
        assert (tmp_path / project_id / result["scenes"][0]["clip"]).is_file()

        changed = client.post(f"/projects/{project_id}/scenes/1/regenerate", json={
            "visual_prompt": "A new preview prompt", "narration": "Updated narration for scene one."
        })
        assert changed.status_code == 202
        result = wait_for_completion(client, project_id)
        assert result["status"] == "complete", result["error"]
        assert result["revision"] == 1
        assert result["scenes"][0]["visual_prompt"] == "A new preview prompt"
        assert "Updated narration" in (tmp_path / project_id / "captions.srt").read_text()

