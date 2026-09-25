"""CPU coverage for narration, native and hybrid scene audio routing."""
from pathlib import Path

import pytest

from app import core, media


def make_clip(path: Path, seconds: float, with_audio: bool) -> None:
    args = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=256x448:r=24:d={seconds}",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
    else:
        args += ["-an"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)]
    core.run(*args)


def make_voice(path: Path, seconds: float) -> None:
    core.run("ffmpeg", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=880:sample_rate=24000:duration={seconds}",
             "-c:a", "pcm_s16le", str(path))


def manifest(scene: dict) -> dict:
    return {
        "request": core.Request(topic="Audio routing", duration=10, width=256, height=448).model_dump(),
        "scenes": [scene],
        "caption_style": "classic",
    }


def prepare_captions(folder: Path, scene: dict) -> None:
    scene["start"] = 0.0
    core.captions([scene], folder, whisper=False)


def test_native_audio_renders_without_voice_file(tmp_path: Path):
    (tmp_path / "clips").mkdir()
    clip = tmp_path / "clips/native.mp4"
    make_clip(clip, 2, with_audio=True)
    scene = {
        "id": 1, "duration": 2.0, "narration": "Native spoken scene.",
        "visual_prompt": "A speaker talks to camera", "clip": "clips/native.mp4",
        "voice": None, "audio_mode": "native",
    }
    prepare_captions(tmp_path, scene)

    core.render(tmp_path, manifest(scene))

    result = media.validate_final(tmp_path / "final.mp4", 256, 448, 2.0)
    assert result.duration == pytest.approx(2.0, abs=0.2)
    assert media.has_audio(tmp_path / "final.mp4")


def test_hybrid_audio_mixes_native_track_and_narration(tmp_path: Path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "voice").mkdir()
    clip = tmp_path / "clips/hybrid.mp4"
    voice = tmp_path / "voice/hybrid.wav"
    make_clip(clip, 2, with_audio=True)
    make_voice(voice, 2)
    scene = {
        "id": 1, "duration": 2.0, "narration": "Narration over ambience.",
        "visual_prompt": "Wind moves through a forest", "clip": "clips/hybrid.mp4",
        "voice": "voice/hybrid.wav", "audio_mode": "hybrid",
    }
    prepare_captions(tmp_path, scene)

    core.render(tmp_path, manifest(scene))

    media.validate_final(tmp_path / "final.mp4", 256, 448, 2.0)
    assert media.has_audio(tmp_path / "final.mp4")


def test_native_audio_rejects_video_only_clip(tmp_path: Path):
    (tmp_path / "clips").mkdir()
    clip = tmp_path / "clips/silent.mp4"
    make_clip(clip, 2, with_audio=False)
    scene = {
        "id": 1, "duration": 2.0, "narration": "This requires native audio.",
        "visual_prompt": "A silent test card", "clip": "clips/silent.mp4",
        "voice": None, "audio_mode": "native",
    }
    prepare_captions(tmp_path, scene)

    with pytest.raises(media.MediaValidationError, match="clip has no audio"):
        core.render(tmp_path, manifest(scene))
