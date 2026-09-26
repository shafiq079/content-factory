"""Wan provider contracts run on CPU with fake GPU/model objects."""
import json
import sys
import types
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import core, media, providers
from app.main import app
from app.wan_video import Wan22Video
from test_pipeline import wait_for_completion


def test_wan_preflight_requires_local_checkout_and_complete_checkpoint(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WAN22_REPO", raising=False)
    monkeypatch.delenv("WAN22_CHECKPOINT", raising=False)
    monkeypatch.setattr(core, "ROOT", tmp_path)
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Moon surface", "video_provider": "wan22"})
        assert response.status_code == 422
        assert "WAN22_REPO" in response.text
        assert not list(tmp_path.glob("*/timeline.json"))

    repo = tmp_path / "Wan2.2"
    model = tmp_path / "model"
    (repo / "wan").mkdir(parents=True)
    (repo / "wan/textimage2video.py").touch()
    (model / "google/umt5-xxl").mkdir(parents=True)
    for name in ("config.json", "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.2_VAE.pth"):
        (model / name).touch()
    index = model / "diffusion_pytorch_model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map": {"layer": "missing.safetensors"}}))
    monkeypatch.setenv("WAN22_REPO", str(repo))
    monkeypatch.setenv("WAN22_CHECKPOINT", str(model))
    with pytest.raises(RuntimeError, match="model shards"):
        Wan22Video.configured_paths()
    (model / "missing.safetensors").touch()
    assert Wan22Video.configured_paths() == (repo, model)


def test_wan_runtime_reuse_portrait_landscape_and_duration(monkeypatch, tmp_path: Path):
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True, current_device=lambda: 0)
    fake_torch.inference_mode = nullcontext
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(Wan22Video, "configured_paths", staticmethod(lambda: (tmp_path, tmp_path)))
    monkeypatch.setattr(Wan22Video, "_runtime", None)
    monkeypatch.setattr(Wan22Video, "_runtime_key", None)
    loads, calls, evictions = [], [], []
    monkeypatch.setattr(core.LTX25Video, "release_runtime", classmethod(lambda cls: evictions.append("ltx")))

    class FakeVideo:
        def __getitem__(self, key):
            assert key is None
            return self

    class FakeModel:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return FakeVideo()

    def fake_save_video(**kwargs):
        # Mimic the official video-only 24 fps file without model weights.
        seconds = calls[-1]["frame_num"] / kwargs["fps"]
        core.run("ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=256x448:r=24:d={seconds}",
                 "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", kwargs["save_file"])

    def fake_build(repo, model, device):
        loads.append((repo, model, device))
        return FakeModel(), types.SimpleNamespace(sample_shift=5.0, sample_steps=50,
                                                  sample_guide_scale=5.0, sample_fps=24), fake_save_video

    monkeypatch.setattr(Wan22Video, "_build_runtime", staticmethod(fake_build))
    monkeypatch.setenv("WAN22_SEED", "100")
    generator = Wan22Video()
    first = core.Request(topic="Forest landscapes", video_provider="wan22", width=256, height=448)
    second = first.model_copy(update={"width": 448, "height": 256})
    for scene, request in (({"id": 1, "duration": 8.0, "visual_prompt": "A forest", "audio_mode": "narration"}, first),
                           ({"id": 2, "duration": 3.0, "visual_prompt": "A mountain", "audio_mode": "narration"}, second)):
        path = tmp_path / f"scene-{scene['id']}.mp4"
        generator.generate(scene, path, request)
        media.validate_clip(path, scene["duration"])
        assert not media.has_audio(path)
        assert not list(tmp_path.glob("*.wan-raw.mp4"))
    assert len(loads) == 1
    assert evictions == ["ltx", "ltx"]
    assert [item["seed"] for item in calls] == [100, 101]
    assert calls[0]["size"] == (704, 1280) and calls[0]["frame_num"] == 121
    assert calls[1]["size"] == (1280, 704) and calls[1]["frame_num"] % 4 == 1


def test_wan_project_renders_and_regenerates_one_scene_with_fake_adapter(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    calls = []

    class FakeWan:
        def generate(self, scene, output, request):
            assert request.video_provider == "wan22"
            assert scene["audio_mode"] == "narration"
            calls.append(scene["id"])
            core.PreviewVideo().generate(scene, output, request)

    monkeypatch.setitem(providers.REGISTRY["video"], "wan22", providers.Adapter(FakeWan, providers.ready))
    with TestClient(app) as client:
        created = client.post("/projects", json={"topic": "Ocean light", "duration": 10,
                                                 "width": 256, "height": 448, "video_provider": "wan22"})
        assert created.status_code == 202, created.text
        pid = created.json()["id"]
        original = wait_for_completion(client, pid)
        assert original["status"] == "complete", original.get("error")
        assert calls == [1, 2]
        assert all(scene["audio_mode"] == "narration" for scene in original["scenes"])
        assert all(not media.has_audio(tmp_path / pid / scene["clip"]) for scene in original["scenes"])
        assert all((tmp_path / pid / scene["voice"]).is_file() for scene in original["scenes"])
        media.validate_final(tmp_path / pid / "final.mp4", 256, 448, 10)
        assert client.post(f"/projects/{pid}/scenes/1/audio-mode", json={"audio_mode": "hybrid"}).status_code == 422
        assert client.post(f"/projects/{pid}/scenes/batch", json={"operation": "set_audio_mode",
                           "scene_ids": [1, 2], "audio_mode": "hybrid"}).status_code == 422
        assert core.load(pid)["status"] == "complete"
        invalid = client.post(f"/projects/{pid}/scenes/1/regenerate", json={"audio_mode": "native"})
        assert invalid.status_code == 422
        changed = client.post(f"/projects/{pid}/scenes/1/regenerate", json={"visual_prompt": "Sunlight on the sea"})
        assert changed.status_code == 202, changed.text
        result = wait_for_completion(client, pid)
        assert result["status"] == "complete", result.get("error")
        assert calls == [1, 2, 1]
        assert result["scenes"][0]["clip"] != original["scenes"][0]["clip"]
        assert (tmp_path / pid / original["scenes"][0]["clip"]).is_file()
