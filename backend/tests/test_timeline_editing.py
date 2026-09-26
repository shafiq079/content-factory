"""CPU timeline editing: scene order and non-destructive seam transitions."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import contracts, core, media, providers
from app.main import app
from test_pipeline import wait_for_completion


def test_transition_and_reorder_rerender_without_video_generation(monkeypatch, tmp_path: Path):
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
            "topic": "Timeline editing",
            "duration": 10,
            "width": 256,
            "height": 448,
        })
        assert created.status_code == 202, created.text
        project_id = created.json()["id"]
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert [scene["id"] for scene in project["scenes"]] == [1, 2]
        initial_calls = list(video_calls)
        initial_revision = project["revision"]

        changed = client.post(f"/projects/{project_id}/timeline", json={
            "transitions": [{
                "scene_id": 2,
                "transition": "fade",
                "transition_duration": 1.0,
            }]
        })
        assert changed.status_code == 202, changed.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert project["revision"] == initial_revision + 1
        assert project["scenes"][1]["transition"] == "fade"
        assert project["scenes"][1]["transition_duration"] == pytest.approx(1.0)
        assert project["duration_actual"] == pytest.approx(10.0)
        assert video_calls == initial_calls
        assert (tmp_path / project_id / "work/scene-01-transition.mp4").is_file()
        assert (tmp_path / project_id / "work/scene-02-transition.mp4").is_file()
        media.validate_final(tmp_path / project_id / "final.mp4", 256, 448, 10)

        reordered = client.post(f"/projects/{project_id}/timeline", json={
            "scene_order": [2, 1]
        })
        assert reordered.status_code == 202, reordered.text
        project = wait_for_completion(client, project_id)
        assert project["status"] == "complete", project["error"]
        assert [scene["id"] for scene in project["scenes"]] == [2, 1]
        assert project["scenes"][0]["transition"] == "cut"
        assert project["scenes"][0]["transition_duration"] == 0
        assert project["hook"] == project["scenes"][0]["narration"]
        assert project["script"] == " ".join(scene["narration"] for scene in project["scenes"])
        assert project["scenes"][0]["start"] == 0
        assert project["scenes"][1]["start"] == project["scenes"][0]["duration"]
        assert video_calls == initial_calls


def test_timeline_edit_validation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        created = client.post("/projects", json={
            "topic": "Timeline validation",
            "duration": 10,
            "width": 256,
            "height": 448,
        })
        project_id = created.json()["id"]
        complete = wait_for_completion(client, project_id)
        assert complete["status"] == "complete"

        response = client.post(f"/projects/{project_id}/timeline", json={
            "scene_order": [1, 1]
        })
        assert response.status_code == 422

        response = client.post(f"/projects/{project_id}/timeline", json={
            "transitions": [{
                "scene_id": 2,
                "transition": "fade_white",
                "transition_duration": 0.1,
            }]
        })
        assert response.status_code == 422


def test_schema_v3_migrates_transition_defaults():
    legacy = {
        "schema_version": 3,
        "id": "c" * 32,
        "request": {},
        "status": "complete",
        "stage": "complete",
        "scenes": [
            {
                "id": 1,
                "duration": 5,
                "narration": "First scene.",
                "visual_prompt": "First shot",
                "transition": "cut",
            },
            {
                "id": 2,
                "duration": 5,
                "narration": "Second scene.",
                "visual_prompt": "Second shot",
                "transition": "cut",
            },
        ],
        "assets": {},
        "error": None,
        "revision": 0,
        "caption_style": "classic",
        "music": None,
        "sfx": [],
    }
    migrated = contracts.migrate_timeline(legacy)
    assert migrated["schema_version"] == contracts.SCHEMA_VERSION
    assert migrated["scenes"][0]["transition"] == "cut"
    assert migrated["scenes"][0]["transition_duration"] == 0
    assert migrated["scenes"][1]["transition_duration"] == 0
