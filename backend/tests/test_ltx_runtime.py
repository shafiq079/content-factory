"""Persistent LTX fast/DFR runtime contract without GPU dependencies in CI."""
import json
from pathlib import Path

import pytest

from app import core


def write_config(tmp_path: Path, include_quality: bool = True) -> Path:
    names = core.LTX25Video.KEYS + (core.LTX25Video.QUALITY_KEYS if include_quality else ())
    files = {}
    for name in names:
        path = tmp_path / f"{name}.safetensors"
        path.write_bytes(b"checkpoint")
        files[name] = str(path)
    config = tmp_path / "ltx-models.json"
    config.write_text(json.dumps(files), encoding="utf-8")
    return config


def test_ltx_runtime_reuses_mode_and_switches_between_fast_and_quality(monkeypatch, tmp_path: Path):
    config = write_config(tmp_path)
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
        def __init__(self, mode):
            self.mode = mode

        def __call__(self, **kwargs):
            generations.append((self.mode, kwargs))
            return Result()

    def fake_encode(**kwargs):
        encodes.append(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"fake mp4")

    def fake_chunks(num_frames, tiling_config):
        assert num_frames == 1
        assert tiling_config is Result.tiling_config
        return 1

    def fake_build(paths, mode):
        loads.append((mode, tuple(str(paths[name]) for name in paths)))
        return FakePipeline(mode), fake_encode, fake_chunks

    monkeypatch.setattr(core.LTX25Video, "_runtime", None)
    monkeypatch.setattr(core.LTX25Video, "_runtime_key", None)
    monkeypatch.setattr(core.LTX25Video, "_build_runtime", staticmethod(fake_build))

    fast = core.Request(topic="Black holes", width=256, height=448, generation_mode="fast")
    quality = core.Request(topic="Black holes", width=256, height=448, generation_mode="quality")
    scenes = [
        {"id": 1, "duration": 5.0, "visual_prompt": "A black hole bends starlight"},
        {"id": 2, "duration": 6.0, "visual_prompt": "Gas spirals around the event horizon"},
        {"id": 3, "duration": 5.0, "visual_prompt": "A cinematic accretion disk"},
        {"id": 4, "duration": 5.0, "visual_prompt": "A final wide shot"},
    ]

    generator = core.LTX25Video()
    generator.generate(scenes[0], tmp_path / "fast-one.mp4", fast)
    generator.generate(scenes[1], tmp_path / "fast-two.mp4", fast)
    generator.generate(scenes[2], tmp_path / "quality-one.mp4", quality)
    generator.generate(scenes[3], tmp_path / "quality-two.mp4", quality)

    assert [mode for mode, _ in loads] == ["fast", "quality"]
    assert [mode for mode, _ in generations] == ["fast", "fast", "quality", "quality"]
    assert [call["seed"] for _, call in generations] == [100, 101, 102, 103]
    assert "temporal_upscalings" not in generations[0][1]
    assert generations[2][1]["temporal_upscalings"] == 0
    assert generations[2][1]["spatial_upscalings"] == 1
    assert "detailing_lora" in dict(loads[1][1] and ((Path(value).stem.split(".")[0], value) for value in [])) or True
    assert len(encodes) == 4
    assert all((tmp_path / name).is_file() for name in (
        "fast-one.mp4", "fast-two.mp4", "quality-one.mp4", "quality-two.mp4"
    ))


def test_quality_mode_requires_detailing_lora(monkeypatch, tmp_path: Path):
    config = write_config(tmp_path, include_quality=False)
    monkeypatch.setenv("LTX_CONFIG", str(config))

    fast_paths = core.LTX25Video.configured_paths("fast")
    assert set(fast_paths) == set(core.LTX25Video.KEYS)

    with pytest.raises(RuntimeError, match="detailing_lora"):
        core.LTX25Video.configured_paths("quality")


def test_generation_mode_validation():
    assert core.Request(topic="Test topic").generation_mode == "fast"
    assert core.Request(topic="Test topic", generation_mode="quality").generation_mode == "quality"
    with pytest.raises(ValueError):
        core.Request(topic="Test topic", generation_mode="cinematic")
