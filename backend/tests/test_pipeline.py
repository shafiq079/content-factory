"""End-to-end test of the honest CPU preview, including scene regeneration."""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import contracts, core
from app.jobs import JobStore
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
        assert result["schema_version"] == contracts.SCHEMA_VERSION
        assert result["idea"].startswith("Preview placeholder")
        assert result["hook"] == result["scenes"][0]["narration"]
        assert result["script"] == " ".join(scene["narration"] for scene in result["scenes"])
        assert result["research"] == []
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
        assert result["hook"] == "Updated narration for scene one."
        assert result["script"].startswith("Updated narration for scene one.")


def test_restart_reclaims_expired_job(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    store = JobStore(tmp_path)
    store.initialize()
    project = core.create(core.Request(topic="Star formation", duration=10, width=256, height=448))
    store.enqueue_generate(project["id"])
    claimed = store.claim()
    assert claimed and claimed["project_id"] == project["id"]
    # Simulate a dead process: its lease is no longer renewed.
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease_until=0 WHERE project_id=?", (project["id"],))
    with TestClient(app) as client:
        result = wait_for_completion(client, project["id"])
        assert result["status"] == "complete", result["error"]
        assert (tmp_path / project["id"] / "final.mp4").is_file()
    with store.connect() as db:
        row = db.execute("SELECT state,attempts FROM jobs WHERE project_id=?", (project["id"],)).fetchone()
        assert row["state"] == "complete"
        assert row["attempts"] == 2


def test_cancel_queued_job_then_retry(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    store = JobStore(tmp_path)
    store.initialize()
    project = core.create(core.Request(topic="Ocean life", duration=10, width=256, height=448))
    store.enqueue_generate(project["id"])
    assert store.cancel(project["id"])["status"] == "cancelled"
    assert store.retry(project["id"])["status"] == "queued"
    with TestClient(app) as client:
        result = wait_for_completion(client, project["id"])
        assert result["status"] == "complete", result["error"]


def test_retry_reuses_completed_scene(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    original = core.PreviewVideo.generate
    calls = []
    fail_once = {"value": True}

    def sometimes_fails(self, scene, output, request):
        calls.append(scene["id"])
        if scene["id"] == 2 and fail_once["value"]:
            fail_once["value"] = False
            output.write_bytes(b"unfinished video")
            raise RuntimeError("Simulated interruption before scene 2")
        return original(self, scene, output, request)

    monkeypatch.setattr(core.PreviewVideo, "generate", sometimes_fails)
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Rainforest ecology", "duration": 10, "width": 256, "height": 448})
        project_id = response.json()["id"]
        failed = wait_for_completion(client, project_id)
        assert failed["status"] == "failed"
        assert failed["scenes"][0]["status"] == "ready"
        assert (tmp_path / project_id / "clips/scene-02.partial.mp4").read_bytes() == b"unfinished video"
        assert client.post(f"/projects/{project_id}/retry").status_code == 202
        recovered = wait_for_completion(client, project_id)
        assert recovered["status"] == "complete", recovered["error"]
        assert not (tmp_path / project_id / "clips/scene-02.partial.mp4").exists()
        assert calls == [1, 2, 2]
