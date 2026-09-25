"""Persistent LTX runtime contract without requiring GPU dependencies in CI."""
from pathlib import Path

from app import core


def test_ltx_runtime_is_reused_across_scenes(monkeypatch, tmp_path: Path):
    files = {}
    for name in core.LTX25Video.KEYS:
        path = tmp_path / f"{name}.safetensors"
        path.write_bytes(b"checkpoint")
        files[name] = str(path)

    config = tmp_path / "ltx-models.json"
    import json
    config.write_text(json.dumps(files), encoding="utf-8")
    monkeypatch.setenv("LTX_CONFIG", str(config))
    monkeypatch.setenv("LTX_SEED", "100")

    loads = []
    generations = []
    encodes = []

    class Result:
        video = object()
        audio = object()
        num_frames = 1
        tiling_config = object()

    class FakePipeline:
        def __call__(self, **kwargs):
            generations.append(kwargs)
            return Result()

    def fake_encode(**kwargs):
        encodes.append(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"fake mp4")

    def fake_chunks(num_frames, tiling_config):
        assert num_frames == 1
        assert tiling_config is Result.tiling_config
        return 1

    def fake_build(paths):
        loads.append(tuple(str(paths[name]) for name in core.LTX25Video.KEYS))
        return FakePipeline(), fake_encode, fake_chunks

    monkeypatch.setattr(core.LTX25Video, "_runtime", None)
    monkeypatch.setattr(core.LTX25Video, "_runtime_key", None)
    monkeypatch.setattr(core.LTX25Video, "_build_runtime", staticmethod(fake_build))

    request = core.Request(topic="Black holes", width=256, height=448)
    scene_one = {"id": 1, "duration": 5.0, "visual_prompt": "A black hole bends starlight"}
    scene_two = {"id": 2, "duration": 6.0, "visual_prompt": "Gas spirals around the event horizon"}

    core.LTX25Video().generate(scene_one, tmp_path / "one.mp4", request)
    core.LTX25Video().generate(scene_two, tmp_path / "two.mp4", request)

    assert len(loads) == 1
    assert [call["seed"] for call in generations] == [100, 101]
    assert [call["prompt"] for call in generations] == [
        scene_one["visual_prompt"],
        scene_two["visual_prompt"],
    ]
    assert len(encodes) == 2
    assert (tmp_path / "one.mp4").is_file()
    assert (tmp_path / "two.mp4").is_file()
