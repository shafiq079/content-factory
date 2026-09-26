"""Import scene media into immutable, project-scoped editor assets."""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

from . import media


MAX_VIDEO_BYTES = 150 * 1024 * 1024
MAX_VOICE_BYTES = 30 * 1024 * 1024


def _receive(folder: Path, source: BinaryIO, limit: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".import-", dir=folder)
    path = Path(name)
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise media.MediaValidationError(f"Upload exceeds {limit // (1024 * 1024)} MB")
                output.write(chunk)
        if not size:
            raise media.MediaValidationError("Upload is empty")
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def save_clip(folder: Path, scene_id: int, filename: str, source: BinaryIO,
              require_audio: bool) -> str:
    """Decode and transcode an upload into a clean H.264/AAC source clip."""
    if Path((filename or "").replace("\\", "/")).suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise media.MediaValidationError("Video must be MP4, MOV, WebM or MKV")
    directory = folder / "clips" / "imports"
    incoming = _receive(directory, source, MAX_VIDEO_BYTES)
    target = directory / f"scene-{scene_id:02d}-{uuid.uuid4().hex}.mp4"
    try:
        probe = media.inspect(incoming)
        video = media.stream(probe, "video")
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        if not (64 <= width <= 4096 and 64 <= height <= 4096 and 0.3 <= probe["duration"] <= 600):
            raise media.MediaValidationError("Video needs 64–4096 pixel dimensions and a duration of 0.3–600 seconds")
        if any(s.get("codec_type") not in {"video", "audio"} for s in probe["streams"]):
            raise media.MediaValidationError("Video contains an unsupported stream")
        has_audio = any(s.get("codec_type") == "audio" for s in probe["streams"])
        if require_audio and not has_audio:
            raise media.MediaValidationError("Native audio mode requires a video with an audio track")
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(incoming),
                   "-map", "0:v:0"]
        if has_audio:
            command += ["-map", "0:a:0", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
        subprocess.run(command, check=True, capture_output=True, timeout=900)
        media.stream(media.inspect(target), "video")
        if require_audio and not media.has_audio(target):
            raise media.MediaValidationError("Imported clip lost its native audio")
        return target.relative_to(folder).as_posix()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        target.unlink(missing_ok=True)
        raise media.MediaValidationError("Video could not be decoded and imported") from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        incoming.unlink(missing_ok=True)


def save_voice(folder: Path, scene_id: int, filename: str, source: BinaryIO) -> tuple[str, float]:
    """Import a narration file as a PCM WAV so it fits the existing renderer."""
    if Path((filename or "").replace("\\", "/")).suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}:
        raise media.MediaValidationError("Narration must be WAV, MP3, M4A, FLAC, OGG or Opus")
    directory = folder / "voice" / "imports"
    incoming = _receive(directory, source, MAX_VOICE_BYTES)
    target = directory / f"scene-{scene_id:02d}-{uuid.uuid4().hex}.wav"
    try:
        probe = media.inspect(incoming)
        media.stream(probe, "audio")
        if any(s.get("codec_type") != "audio" for s in probe["streams"]) or probe["duration"] > 600:
            raise media.MediaValidationError("Narration must contain audio only and be at most 600 seconds")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(incoming),
                        "-map", "0:a:0", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(target)],
                       check=True, capture_output=True, timeout=900)
        duration = round(media.validate_voice(target, probe["duration"], False).duration, 3)
        return target.relative_to(folder).as_posix(), duration
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        target.unlink(missing_ok=True)
        raise media.MediaValidationError("Narration could not be decoded and imported") from exc
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        incoming.unlink(missing_ok=True)
