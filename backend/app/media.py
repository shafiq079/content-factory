"""Probe actual files before publishing or resuming them."""
from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VideoAsset:
    path: Path
    duration: float
    width: int
    height: int
    codec: str


@dataclass(frozen=True)
class VoiceAsset:
    path: Path
    duration: float
    codec: str
    sample_rate: int


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    text: str


def inspect(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaValidationError(f"Missing or empty media: {path.name}")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("nonpositive duration")
        data["duration"] = duration
        return data
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise MediaValidationError(f"Cannot probe {path.name}: {exc}") from exc


def stream(data: dict, kind: str) -> dict:
    match = next((s for s in data["streams"] if s.get("codec_type") == kind), None)
    if match is None:
        raise MediaValidationError(f"Missing {kind} stream")
    return match


def validate_clip(path: Path, seconds: float) -> VideoAsset:
    data = inspect(path)
    video = stream(data, "video")
    if abs(data["duration"] - seconds) > max(0.75, seconds * 0.15):
        raise MediaValidationError(f"Clip duration {data['duration']:.2f}s differs from planned {seconds:.2f}s")
    if not video.get("width") or not video.get("height"):
        raise MediaValidationError("Clip has no video dimensions")
    return VideoAsset(path, data["duration"], video["width"], video["height"], video.get("codec_name", "unknown"))


def validate_voice(path: Path, seconds: float, silent: bool) -> VoiceAsset:
    data = inspect(path)
    audio = stream(data, "audio")
    if not str(audio.get("codec_name", "")).startswith("pcm_") or int(audio.get("sample_rate", 0)) < 8000 or int(audio.get("channels", 0)) < 1:
        raise MediaValidationError("Voice WAV needs valid PCM audio")
    if silent and abs(data["duration"] - seconds) > max(0.5, seconds * 0.1):
        raise MediaValidationError(f"Silent voice duration {data['duration']:.2f}s differs from planned {seconds:.2f}s")
    if not silent and data["duration"] < 0.3:
        raise MediaValidationError("Generated voice is too short")
    return VoiceAsset(path, data["duration"], audio["codec_name"], int(audio["sample_rate"]))


TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def _seconds(value: str) -> float:
    match = TIME.fullmatch(value)
    if not match or int(match[2]) >= 60 or int(match[3]) >= 60:
        raise MediaValidationError(f"Invalid caption timestamp: {value}")
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def validate_captions(path: Path, duration: float) -> list[CaptionCue]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip())
    if not blocks or not blocks[0]:
        raise MediaValidationError("Captions are empty")
    previous = 0.0
    cues = []
    for i, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(i) or " --> " not in lines[1] or not " ".join(lines[2:]).strip():
            raise MediaValidationError(f"Invalid caption block {i}")
        start, end = map(_seconds, lines[1].split(" --> "))
        if start < previous - 0.002 or end <= start or end > duration + 0.1:
            raise MediaValidationError(f"Caption block {i} is outside the timeline")
        previous = end
        cues.append(CaptionCue(start, end, " ".join(lines[2:])))
    return cues


def validate_final(path: Path, width: int, height: int, duration: float) -> VideoAsset:
    data = inspect(path)
    video, audio = stream(data, "video"), stream(data, "audio")
    if (video.get("codec_name"), video.get("width"), video.get("height")) != ("h264", width, height):
        raise MediaValidationError("Final MP4 needs H.264 at the requested dimensions")
    if audio.get("codec_name") != "aac":
        raise MediaValidationError("Final MP4 needs AAC audio")
    if abs(data["duration"] - duration) > max(0.75, duration * 0.05):
        raise MediaValidationError(f"Final duration {data['duration']:.2f}s differs from timeline {duration:.2f}s")
    return VideoAsset(path, data["duration"], video["width"], video["height"], video["codec_name"])
