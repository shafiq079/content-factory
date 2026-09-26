"""Project-scoped background music and SFX assets plus FFmpeg master mixing."""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

from . import media


MAX_AUDIO_UPLOAD_BYTES = 30 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
)


class AudioAssetError(ValueError):
    pass


def _extension(filename: str) -> str:
    name = Path((filename or "").replace("\\", "/")).name
    if not name or name in {".", ".."} or not name.isprintable():
        raise AudioAssetError("Invalid audio filename")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        supported = ", ".join(ext.removeprefix(".").upper() for ext in SUPPORTED_AUDIO_EXTENSIONS)
        raise AudioAssetError(f"Unsupported audio format; supported formats: {supported}")
    return suffix


def validate_audio_asset(path: Path) -> float:
    """Require a decodable audio-only file and return its duration."""
    try:
        data = media.inspect(path)
        media.stream(data, "audio")
    except media.MediaValidationError as exc:
        raise AudioAssetError(str(exc)) from exc
    if any(item.get("codec_type") == "video" for item in data.get("streams", [])):
        raise AudioAssetError("Audio asset must not contain a video stream")
    return float(data["duration"])


def save_upload(project_dir: Path, category: str, filename: str, source: BinaryIO) -> tuple[str, float]:
    """Stream an upload into the project, validate it, then atomically publish it."""
    if category not in ("music", "sfx"):
        raise ValueError("Unknown audio asset category")
    suffix = _extension(filename)
    target_dir = project_dir / "audio" / category
    target_dir.mkdir(parents=True, exist_ok=True)
    final_name = f"{uuid.uuid4().hex}{suffix}"
    final_path = target_dir / final_name
    temp_path: Path | None = None
    total = 0
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=suffix, dir=target_dir)
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise AudioAssetError("Audio upload must be binary")
                total += len(chunk)
                if total > MAX_AUDIO_UPLOAD_BYTES:
                    raise AudioAssetError("Audio upload exceeds the 30 MB limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total == 0:
            raise AudioAssetError("Audio upload is empty")
        duration = validate_audio_asset(temp_path)
        temp_path.replace(final_path)
        temp_path = None
        return final_path.relative_to(project_dir).as_posix(), duration
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        try:
            source.seek(0)
        except (AttributeError, OSError):
            pass


def remove_asset(project_dir: Path, relative_path: str | None) -> None:
    if not relative_path:
        return
    target = (project_dir / relative_path).resolve()
    root = project_dir.resolve()
    if target.is_relative_to(root / "audio"):
        target.unlink(missing_ok=True)


def _finite_volume(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise AudioAssetError(f"{field} must be between 0 and 1")
    return number


def _finite_seconds(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise AudioAssetError(f"{field} must be zero or greater")
    return number


def mix_master_audio(raw_video: Path, output_video: Path, project_dir: Path,
                     manifest: dict, duration: float) -> bool:
    """Mix timeline BGM/SFX over the already assembled scene audio.

    Returns True when at least one overlay was active. The base timeline audio is
    always the first input and determines the output duration.
    """
    music = manifest.get("music")
    sfx_items = list(manifest.get("sfx") or [])
    inputs: list[tuple[str, dict, Path, float]] = []

    if music and music.get("enabled", True):
        volume = _finite_volume(music.get("volume", 0.2), "Music volume")
        if volume > 0:
            asset = (project_dir / music["asset"]).resolve()
            if not asset.is_relative_to((project_dir / "audio").resolve()):
                raise AudioAssetError("Music asset is outside project audio storage")
            asset_duration = validate_audio_asset(asset)
            inputs.append(("music", music, asset, asset_duration))

    for item in sfx_items:
        if not item.get("enabled", True):
            continue
        volume = _finite_volume(item.get("volume", 0.7), "SFX volume")
        if volume <= 0:
            continue
        start = _finite_seconds(item.get("start", 0), "SFX start")
        if start >= duration:
            continue
        asset = (project_dir / item["asset"]).resolve()
        if not asset.is_relative_to((project_dir / "audio").resolve()):
            raise AudioAssetError("SFX asset is outside project audio storage")
        asset_duration = validate_audio_asset(asset)
        inputs.append(("sfx", item, asset, asset_duration))

    if not inputs:
        return False

    command = ["ffmpeg", "-y", "-i", str(raw_video)]
    input_indexes: list[tuple[int, str, dict, float]] = []
    next_index = 1
    for kind, config, asset, asset_duration in inputs:
        if kind == "music" and config.get("loop", True):
            command += ["-stream_loop", "-1", "-i", str(asset)]
        else:
            command += ["-i", str(asset)]
        input_indexes.append((next_index, kind, config, asset_duration))
        next_index += 1

    filters = ["[0:a]aresample=48000,asetpts=PTS-STARTPTS[base]"]
    labels = ["[base]"]
    for index, kind, config, asset_duration in input_indexes:
        volume = _finite_volume(config.get("volume", 0.2 if kind == "music" else 0.7),
                                "Music volume" if kind == "music" else "SFX volume")
        fade_in = _finite_seconds(config.get("fade_in", 0.0), f"{kind} fade in")
        fade_out = _finite_seconds(config.get("fade_out", 0.0), f"{kind} fade out")

        if kind == "music":
            effective = duration if config.get("loop", True) else min(duration, asset_duration)
            chain = f"[{index}:a]atrim=0:{effective:.3f},asetpts=PTS-STARTPTS,aresample=48000,volume={volume:.5f}"
            if fade_in > 0:
                chain += f",afade=t=in:st=0:d={min(fade_in, effective):.3f}"
            if fade_out > 0 and effective > 0:
                actual = min(fade_out, effective)
                chain += f",afade=t=out:st={max(0.0, effective-actual):.3f}:d={actual:.3f}"
            label = f"music{index}"
        else:
            start = _finite_seconds(config.get("start", 0.0), "SFX start")
            requested_duration = config.get("duration")
            effective = min(asset_duration, max(0.0, duration - start))
            if requested_duration is not None:
                effective = min(effective, _finite_seconds(requested_duration, "SFX duration"))
            chain = f"[{index}:a]atrim=0:{effective:.3f},asetpts=PTS-STARTPTS,aresample=48000,volume={volume:.5f}"
            if fade_in > 0:
                chain += f",afade=t=in:st=0:d={min(fade_in, effective):.3f}"
            if fade_out > 0 and effective > 0:
                actual = min(fade_out, effective)
                chain += f",afade=t=out:st={max(0.0, effective-actual):.3f}:d={actual:.3f}"
            delay_ms = round(start * 1000)
            chain += f",adelay={delay_ms}:all=1"
            label = f"sfx{index}"
        filters.append(f"{chain}[{label}]")
        labels.append(f"[{label}]")

    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95[aout]"
    )
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}", str(output_video),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="ignore")[-1200:] if isinstance(exc.stderr, bytes) else str(exc.stderr)[-1200:]
        raise AudioAssetError(f"FFmpeg audio mix failed: {detail}") from exc
    return True
