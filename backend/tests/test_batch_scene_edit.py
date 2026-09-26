"""One recoverable batch job, with no video generation or partial timeline edits."""
from pathlib import Path

from fastapi.testclient import TestClient

from app import core, providers
from app.jobs import JobStore
from app.main import app
from test_pipeline import wait_for_completion


def project(client: TestClient) -> dict:
    response = client.post("/projects", json={"topic": "Batch editing", "duration": 10,
                                              "width": 256, "height": 448})
    assert response.status_code == 202
    complete = wait_for_completion(client, response.json()["id"])
    assert complete["status"] == "complete", complete.get("error")
    return complete


def post(client: TestClient, project_id: str, **edit):
    return client.post(f"/projects/{project_id}/scenes/batch", json=edit)


def test_transition_batch_validates_every_scene_and_renders_once(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    with TestClient(app) as client:
        initial = project(client)
        pid = initial["id"]
        before = core.claim_review_fingerprint(initial)
        calls = {"render": 0, "captions": 0}
        original_render, original_captions = core.render, core.captions

        def render(*args):
            calls["render"] += 1
            return original_render(*args)

        def captions(*args):
            calls["captions"] += 1
            return original_captions(*args)

        monkeypatch.setattr(core, "render", render)
        monkeypatch.setattr(core, "captions", captions)
        monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(
            lambda: (_ for _ in ()).throw(AssertionError("Batch invoked video provider")), providers.ready))

        for ids in ([], [2, 2], [2, 99]):
            assert post(client, pid, operation="set_transition", scene_ids=ids,
                        transition="fade", transition_duration=0.8).status_code == 422
        assert post(client, pid, operation="set_transition", scene_ids=[1, 2],
                    transition="fade", transition_duration=0.8).status_code == 422
        assert post(client, pid, operation="set_transition", scene_ids=[2],
                    transition="fade", transition_duration=0.1).status_code == 422
        assert post(client, pid, operation="set_transition", scene_ids=[2],
                    transition="cut", transition_duration=0.8).status_code == 422
        assert post(client, pid, operation="set_transition", scene_ids=[2],
                    transition="fade", transition_duration=0.8, unexpected=True).status_code == 422
        assert core.load(pid)["status"] == "complete"
        assert calls == {"render": 0, "captions": 0}

        response = post(client, pid, operation="set_transition", scene_ids=[2],
                        transition="fade_white", transition_duration=0.8)
        assert response.status_code == 202, response.text
        assert response.json()["pending_job"]["execution"] == "Render only"
        edited = wait_for_completion(client, pid)
        assert edited["status"] == "complete", edited.get("error")
        assert edited["revision"] == initial["revision"] + 1
        assert edited["scenes"][0]["transition"] == "cut"
        assert edited["scenes"][1]["transition"] == "fade_white"
        assert edited["duration_actual"] == initial["duration_actual"]
        assert calls == {"render": 1, "captions": 1}
        assert core.claim_review_fingerprint(edited) == before
        assert edited["claim_review"] == initial["claim_review"]


def test_audio_batch_preflight_and_generate_only_missing_voice(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    with TestClient(app) as client:
        initial = project(client)
        pid = initial["id"]
        folder = tmp_path / pid
        manifest = core.load(pid)
        manifest["scenes"][0]["voice"] = None
        core.atomic_write(folder / "timeline.json", manifest)
        original_voice = manifest["scenes"][1]["voice"]

        native = post(client, pid, operation="set_audio_mode", scene_ids=[1, 2], audio_mode="native")
        assert native.status_code == 422
        assert "Scene 1" in native.text
        assert core.load(pid)["status"] == "complete"

        generated = []
        class LoggedVoice:
            def generate(self, text, output, seconds, language, voice_id="", speed=1):
                generated.append(output)
                core.SilentVoice().generate(text, output, seconds, language, voice_id, speed)
        monkeypatch.setitem(providers.REGISTRY["voice"], "silent", providers.Adapter(LoggedVoice, providers.ready))
        monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(
            lambda: (_ for _ in ()).throw(AssertionError("Batch invoked video provider")), providers.ready))

        response = post(client, pid, operation="set_audio_mode", scene_ids=[1, 2], audio_mode="hybrid")
        assert response.status_code == 202, response.text
        assert response.json()["pending_job"]["execution"] == "TTS + render"
        edited = wait_for_completion(client, pid)
        assert edited["status"] == "complete", edited.get("error")
        assert len(generated) == 1
        assert all(scene["audio_mode"] == "hybrid" for scene in edited["scenes"])
        assert edited["scenes"][1]["voice"] == original_voice
        assert edited["scenes"][0]["voice"] and (folder / edited["scenes"][0]["voice"]).is_file()
        assert edited["scenes"][1]["start"] == edited["scenes"][0]["duration"]
        assert edited["duration_actual"] == round(sum(s["duration"] for s in edited["scenes"]), 3)
        assert core.claim_review_fingerprint(edited) == core.claim_review_fingerprint(initial)


def test_failed_batch_keeps_original_and_recovers_after_restart(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setenv("CAPTION_PROVIDER", "script")
    with TestClient(app) as client:
        initial = project(client)
    pid = initial["id"]
    folder = tmp_path / pid
    manifest = core.load(pid)
    for scene in manifest["scenes"]:
        scene["voice"] = None
    core.atomic_write(folder / "timeline.json", manifest)
    original_captions = (folder / "captions.srt").read_bytes()
    original_final = (folder / "final.mp4").read_bytes()
    before = core.load(pid)

    store = JobStore(tmp_path)
    queued = store.enqueue_batch(pid, {"operation": "set_audio_mode", "scene_ids": [1, 2], "audio_mode": "hybrid"})
    assert queued["status"] == "queued"
    with store.connect() as db:
        db.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
    store.initialize()
    claimed = store.claim()
    assert claimed and claimed["kind"] == "batch"

    called = []
    class FailingVoice:
        def generate(self, text, output, seconds, language, voice_id="", speed=1):
            called.append(output)
            if len(called) == 2:
                raise RuntimeError("second voice failed")
            core.SilentVoice().generate(text, output, seconds, language, voice_id, speed)
    monkeypatch.setitem(providers.REGISTRY["voice"], "silent", providers.Adapter(FailingVoice, providers.ready))
    core.batch_work(pid)
    failed = core.load(pid)
    assert failed["status"] == "failed" and "second voice failed" in failed["error"]
    assert failed["scenes"] == before["scenes"]
    assert failed["revision"] == before["revision"]
    assert (folder / "captions.srt").read_bytes() == original_captions
    assert (folder / "final.mp4").read_bytes() == original_final
    assert not any(path.exists() for path in called)
    store.finish(pid, claimed["token"], "failed")
    store.retry(pid)
    monkeypatch.setitem(providers.REGISTRY["voice"], "silent", providers.Adapter(core.SilentVoice, providers.ready))
    core.batch_work(pid)
    complete = core.load(pid)
    assert complete["status"] == "complete", complete.get("error")
    assert all(scene["audio_mode"] == "hybrid" for scene in complete["scenes"])
    assert complete["revision"] == before["revision"] + 1
