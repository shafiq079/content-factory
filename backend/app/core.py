"""Local project pipeline. All source assets and metadata remain editable."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field, field_validator

from . import contracts, media


ROOT = Path(os.getenv("PROJECTS_DIR", Path(__file__).resolve().parents[2] / "projects")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)


class Request(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    duration: int = Field(default=30, ge=10, le=120)
    language: str = Field(default="English", min_length=2, max_length=40)
    style: str = Field(default="Cinematic documentary", max_length=120)
    instructions: str = Field(default="", max_length=1000)
    width: int = Field(default=1080, ge=256, le=2160)
    height: int = Field(default=1920, ge=256, le=3840)
    video_provider: str = "preview"
    voice_provider: str = "silent"
    planner_provider: str = "template"

    @field_validator("video_provider")
    @classmethod
    def video_choice(cls, value: str) -> str:
        if value not in ("preview", "ltx25"):
            raise ValueError("Choose preview or ltx25")
        return value

    @field_validator("voice_provider")
    @classmethod
    def voice_choice(cls, value: str) -> str:
        if value not in ("silent", "kokoro"):
            raise ValueError("Choose silent or kokoro")
        return value

    @field_validator("planner_provider")
    @classmethod
    def planner_choice(cls, value: str) -> str:
        if value not in ("template", "ollama"):
            raise ValueError("Choose template or ollama")
        return value


def run(*args: str) -> None:
    subprocess.run(list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)


def probe_duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", str(path)], check=True, capture_output=True, text=True)
    return float(p.stdout.strip())


def atomic_write(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class TextGenerator(ABC):
    @abstractmethod
    def plan(self, request: Request) -> list[dict]: ...


class TemplatePlanner(TextGenerator):
    """Deterministic pipeline smoke test, not factual research or an LLM."""
    def plan(self, request: Request) -> list[dict]:
        count = max(2, math.ceil(request.duration / 7))
        angles = ["opening wide shot", "revealing close up", "detail and movement", "a surprising perspective", "a final cinematic view"]
        return [{"id": i + 1, "duration": round(request.duration / count, 3),
                 "narration": f"{request.topic}. Part {i+1}: explore a different perspective on this subject.",
                 "visual_prompt": f"{request.style}, {angles[i % len(angles)]} of {request.topic}. {request.instructions} No text, no logos. Vertical composition.",
                 "camera": angles[i % len(angles)], "transition": "cut", "status": "pending"}
                for i in range(count)]


class OllamaPlanner(TextGenerator):
    def plan(self, request: Request) -> list[dict]:
        n = max(2, math.ceil(request.duration / 7))
        prompt = (f"Create {n} distinct video scenes about {request.topic}. Language: {request.language}. "
                  f"Style: {request.style}. Extra instructions: {request.instructions}. "
                  f"Each scene is about {request.duration/n:.1f} seconds. Return JSON object with key scenes: "
                  "array of exactly this many objects, each with narration (a short speakable sentence), "
                  "visual_prompt (concrete visual action), camera, transition. Avoid unsupported factual claims.")
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
        # Local-only by default; credentials and remote endpoints are intentionally not forwarded.
        from urllib.parse import urlparse
        if urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("OLLAMA_URL must refer to a local service")
        payload = json.dumps({"model": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"), "prompt": prompt, "format": "json", "stream": False}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}), timeout=180) as r:
            data = json.loads(json.loads(r.read())["response"])
        scenes = data["scenes"]
        if len(scenes) != n:
            raise ValueError(f"Planner returned {len(scenes)} scenes; expected {n}")
        clean = []
        for i, s in enumerate(scenes):
            if not all(isinstance(s.get(k), str) and s[k].strip() for k in ("narration", "visual_prompt")):
                raise ValueError("Planner returned an incomplete scene")
            clean.append({"id": i+1, "duration": round(request.duration/n, 3),
                          "narration": s["narration"], "visual_prompt": s["visual_prompt"],
                          "camera": s.get("camera", "static"), "transition": s.get("transition", "cut"), "status": "pending"})
        return clean


class VideoGenerator(ABC):
    @abstractmethod
    def generate(self, scene: dict, output: Path, request: Request) -> None: ...


class PreviewVideo(VideoGenerator):
    """Obvious animated test cards, never presented as model-generated footage."""
    def generate(self, scene: dict, output: Path, request: Request) -> None:
        d = scene["duration"]
        run("ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x{['141e38','254158','3a3157','234742'][((scene['id']-1)%4)]}:s=540x960:r=24:d={d}",
            "-vf", f"drawbox=x=200:y=220:w=140:h=140:color=0x67d6bb@0.5:t=fill,scale={request.width}:{request.height}",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(d), str(output))


class LTX25Video(VideoGenerator):
    """Invokes official ltx_pipelines.distilled on a separately configured GPU host."""
    KEYS = {"transformer": "--transformer-path", "text_encoder": "--text-encoder-path", "video_vae": "--video-vae-path", "audio_vae": "--audio-vae-path", "upscaler": "--spatial-upsampler-path"}

    def generate(self, scene: dict, output: Path, request: Request) -> None:
        config = Path(os.getenv("LTX_CONFIG", "ltx-models.json"))
        if not config.is_file():
            raise RuntimeError(f"LTX_CONFIG missing: {config}. See ltx-models.example.json")
        paths = json.loads(config.read_text())
        args = [sys.executable, "-m", "ltx_pipelines.distilled"]
        for name, flag in self.KEYS.items():
            path = Path(paths[name]).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"Missing LTX checkpoint: {name}: {path}")
            args += [flag, str(path)]
        fps = 24
        frames = max(9, int(scene["duration"] * fps / 8) * 8 + 1)
        args += ["--prompt", scene["visual_prompt"], "--height", str(math.ceil(request.height/64)*64),
                 "--width", str(math.ceil(request.width/64)*64), "--num-frames", str(frames),
                 "--frame-rate", str(fps), "--output-path", str(output)]
        subprocess.run(args, check=True, timeout=3600)
        if not output.is_file():
            raise RuntimeError("LTX command exited without a video")


class VoiceGenerator(ABC):
    @abstractmethod
    def generate(self, text: str, output: Path, seconds: float, language: str) -> None: ...


class SilentVoice(VoiceGenerator):
    def generate(self, text: str, output: Path, seconds: float, language: str) -> None:
        run("ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(seconds), "-c:a", "pcm_s16le", str(output))


class KokoroVoice(VoiceGenerator):
    def generate(self, text: str, output: Path, seconds: float, language: str) -> None:
        from kokoro import KPipeline
        import numpy as np
        import soundfile as sf
        codes = {"english": "a", "british english": "b", "spanish": "e", "french": "f", "hindi": "h", "italian": "i", "japanese": "j", "portuguese": "p", "chinese": "z"}
        code = codes.get(language.lower())
        if not code:
            raise ValueError(f"Kokoro language not configured: {language}")
        voice = os.getenv("KOKORO_VOICE", "af_heart" if code == "a" else {"b":"bf_emma","h":"hf_alpha","e":"ef_dora","f":"ff_siwis","i":"if_sara","j":"jf_alpha","p":"pf_dora","z":"zf_xiaobei"}[code])
        audio = [chunk for _, _, chunk in KPipeline(lang_code=code)(text, voice=voice)]
        if not audio:
            raise RuntimeError("Kokoro produced no audio")
        sf.write(output, np.concatenate(audio), 24000)


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def captions(scenes: list[dict], project_dir: Path, whisper: bool) -> None:
    lines = []
    index = 1
    if whisper:
        from faster_whisper import WhisperModel
        model = WhisperModel(os.getenv("WHISPER_MODEL", "small"), device=os.getenv("WHISPER_DEVICE", "cpu"), compute_type=os.getenv("WHISPER_COMPUTE", "int8"))
    for scene in scenes:
        if whisper:
            segments, _ = model.transcribe(str(project_dir / scene["voice"]), word_timestamps=True)
            units = [(w.start, w.end, w.word.strip()) for seg in segments for w in (seg.words or [])]
            # Empty transcription fails visibly; it must never produce deceptive subtitles.
            if not units:
                raise RuntimeError(f"No transcription for scene {scene['id']}")
        else:
            # Script-based timing is only an estimate for silent previews.
            words = scene["narration"].split()
            units = [(i*scene["duration"]/max(1,len(words)), (i+1)*scene["duration"]/max(1,len(words)), word) for i,word in enumerate(words)]
        offset = scene["start"]
        for i in range(0, len(units), 5):
            group = units[i:i+5]
            lines += [str(index), f"{srt_time(offset+group[0][0])} --> {srt_time(offset+group[-1][1])}", " ".join(x[2] for x in group), ""]
            index += 1
    (project_dir / "captions.srt").write_text("\n".join(lines), encoding="utf-8")
    media.validate_captions(project_dir / "captions.srt", sum(s["duration"] for s in scenes))


def render(project_dir: Path, manifest: dict) -> None:
    req = Request.model_validate(manifest["request"])
    normalized = []
    for s in manifest["scenes"]:
        target = project_dir / "work" / f"scene-{s['id']:02d}.mp4"
        target.parent.mkdir(exist_ok=True)
        # Trim or loop a clip to the actual narration duration. Keep scene source untouched.
        run("ffmpeg", "-y", "-stream_loop", "-1", "-i", str(project_dir / s["clip"]), "-i", str(project_dir / s["voice"]),
            "-vf", f"scale={req.width}:{req.height}:force_original_aspect_ratio=increase,crop={req.width}:{req.height},fps=24,setsar=1",
            "-map", "0:v:0", "-map", "1:a:0", "-t", str(s["duration"]), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(target))
        normalized.append(target)
    list_file = project_dir / "work" / "concat.txt"
    list_file.write_text("".join(f"file '{p.name}'\n" for p in normalized))
    raw = project_dir / "work" / "joined.mp4"
    run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(raw))
    # Make SRT subtitle path safe in ffmpeg's filtergraph by running in project cwd.
    subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-vf", "subtitles=captions.srt:force_style='FontSize=15,Alignment=2,MarginV=125,Outline=2'",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", "final.partial.mp4"],
                   check=True, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    media.validate_final(project_dir / "final.partial.mp4", req.width, req.height, sum(s["duration"] for s in manifest["scenes"]))
    (project_dir / "final.partial.mp4").replace(project_dir / "final.mp4")


def export_otio(project_dir: Path, scenes: list[dict]) -> None:
    try:
        import opentimelineio as otio
    except ImportError:
        return
    timeline = otio.schema.Timeline(name="Content Factory")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    for s in scenes:
        rate = 24
        media = otio.schema.ExternalReference(target_url=(project_dir / s["clip"]).as_uri())
        span = otio.opentime.TimeRange(otio.opentime.RationalTime(0,rate), otio.opentime.RationalTime(round(s["duration"]*rate),rate))
        track.append(otio.schema.Clip(name=f"Scene {s['id']}", media_reference=media, source_range=span, metadata={"narration": s["narration"], "prompt": s["visual_prompt"]}))
    otio.adapters.write_to_file(timeline, str(project_dir / "timeline.otio"))


def project_path(project_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise ValueError("Invalid project ID")
    path = ROOT / project_id
    if not path.is_dir():
        raise FileNotFoundError(project_id)
    return path


def load(project_id: str) -> dict:
    path = project_path(project_id) / "timeline.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    upgraded = contracts.migrate_timeline(data)
    if data.get("schema_version") != contracts.SCHEMA_VERSION:
        atomic_write(path, upgraded)
    return upgraded


def create(request: Request) -> dict:
    project_id = uuid.uuid4().hex
    folder = ROOT / project_id
    folder.mkdir()
    manifest = {"schema_version": contracts.SCHEMA_VERSION, "id": project_id, "request": request.model_dump(), "status": "queued", "stage": "queued", "scenes": [], "assets": {}, "error": None, "revision": 0}
    atomic_write(folder / "timeline.json", manifest)
    return manifest


class JobCancelled(Exception):
    pass


def process(project_id: str, is_cancelled: Callable[[], bool] = lambda: False) -> None:
    from . import providers

    folder = project_path(project_id)
    manifest = load(project_id)
    req = Request.model_validate(manifest["request"])
    def save(stage: str) -> None:
        manifest["stage"] = stage
        atomic_write(folder / "timeline.json", manifest)
    def check() -> None:
        if is_cancelled():
            raise JobCancelled("Cancellation requested")
    try:
        if manifest["status"] == "complete" and (folder / "final.mp4").is_file():
            media.validate_final(folder / "final.mp4", req.width, req.height, sum(s["duration"] for s in manifest["scenes"]))
            return
        manifest["status"] = "running"
        manifest["error"] = None
        check()
        providers.preflight(req)
        if not manifest["scenes"]:
            save("planning")
            planner = providers.make("planner", req.planner_provider)
            manifest["scenes"] = contracts.validate_scenes(planner.plan(req))
            save("scene plan ready")
        scenes = manifest["scenes"]
        video = providers.make("video", req.video_provider)
        voice = providers.make("voice", req.voice_provider)
        (folder / "clips").mkdir(exist_ok=True)
        (folder / "voice").mkdir(exist_ok=True)
        start = 0.0
        for scene in scenes:
            check()
            scene["start"] = round(start, 3)
            scene["clip"] = f"clips/scene-{scene['id']:02d}.mp4"
            scene["voice"] = f"voice/scene-{scene['id']:02d}.wav"
            clip, audio = folder / scene["clip"], folder / scene["voice"]
            if clip.is_file():
                try:
                    media.validate_clip(clip, scene["duration"])
                except media.MediaValidationError:
                    clip.unlink()
            if not clip.is_file():
                save(f"generating scene {scene['id']}/{len(scenes)}")
                partial = clip.with_name(clip.stem + ".partial.mp4")
                partial.unlink(missing_ok=True)
                video.generate(scene, partial, req)
                media.validate_clip(partial, scene["duration"])
                partial.replace(clip)
            check()
            if audio.is_file():
                try:
                    media.validate_voice(audio, scene["duration"], req.voice_provider == "silent")
                except media.MediaValidationError:
                    audio.unlink()
            if not audio.is_file():
                save(f"voicing scene {scene['id']}/{len(scenes)}")
                partial = audio.with_name(audio.stem + ".partial.wav")
                partial.unlink(missing_ok=True)
                voice.generate(scene["narration"], partial, scene["duration"], req.language)
                media.validate_voice(partial, scene["duration"], req.voice_provider == "silent")
                partial.replace(audio)
            if req.voice_provider == "kokoro" and scene["status"] != "ready":
                scene["duration"] = max(probe_duration(audio), 0.3)
            scene["status"] = "ready"
            start += scene["duration"]
            atomic_write(folder / "timeline.json", manifest)
        manifest["duration_actual"] = round(start,3)
        check()
        save("captions")
        captions(scenes, folder, providers.caption_choice(req) == "whisper")
        check()
        save("rendering")
        render(folder, manifest)
        export_otio(folder, scenes)
        manifest["assets"] = {"final": "final.mp4", "captions": "captions.srt", "timeline": "timeline.json"}
        manifest["status"] = "complete"
        save("complete")
    except JobCancelled:
        manifest.update(status="cancelled", error=None)
        save("cancelled")
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        save("failed")


def regenerate_work(project_id: str, scene_id: int, is_cancelled: Callable[[], bool] = lambda: False) -> None:
    from . import providers

    folder = project_path(project_id)
    manifest = load(project_id)
    scene = next(s for s in manifest["scenes"] if s["id"] == scene_id)
    req = Request.model_validate(manifest["request"])
    def check() -> None:
        if is_cancelled():
            raise JobCancelled("Cancellation requested")
    try:
        check()
        providers.preflight(req)
        manifest.update(status="running", stage=f"regenerating scene {scene_id}", error=None)
        atomic_write(folder / "timeline.json", manifest)
        video = providers.make("video", req.video_provider)
        voice = providers.make("voice", req.voice_provider)
        clip, audio = folder / scene["clip"], folder / scene["voice"]
        if not clip.is_file():
            partial = clip.with_name(clip.stem + ".partial.mp4")
            partial.unlink(missing_ok=True)
            video.generate(scene, partial, req)
            media.validate_clip(partial, scene["duration"])
            partial.replace(clip)
        else:
            media.validate_clip(clip, scene["duration"])
        check()
        if not audio.is_file():
            partial = audio.with_name(audio.stem + ".partial.wav")
            partial.unlink(missing_ok=True)
            voice.generate(scene["narration"], partial, scene["duration"], req.language)
            media.validate_voice(partial, scene["duration"], req.voice_provider == "silent")
            partial.replace(audio)
        else:
            media.validate_voice(audio, scene["duration"], req.voice_provider == "silent")
        if req.voice_provider == "kokoro" and scene["status"] != "ready":
            scene["duration"] = probe_duration(audio)
        scene["status"] = "ready"
        atomic_write(folder / "timeline.json", manifest)
        check()
        start = 0.0
        for s in manifest["scenes"]:
            s["start"] = round(start, 3)
            start += s["duration"]
        manifest["duration_actual"] = round(start,3)
        captions(manifest["scenes"], folder, providers.caption_choice(req) == "whisper")
        check()
        render(folder, manifest)
        export_otio(folder, manifest["scenes"])
        manifest["revision"] = manifest.get("revision", 0) + 1
        manifest.update(status="complete", stage="complete", error=None)
    except JobCancelled:
        manifest.update(status="cancelled", stage="cancelled", error=None)
    except Exception as exc:
        manifest.update(status="failed", stage="failed", error=f"{type(exc).__name__}: {exc}")
    atomic_write(folder / "timeline.json", manifest)


def import_clip_work(project_id: str, scene_id: int, is_cancelled: Callable[[], bool] = lambda: False) -> None:
    """Use uploaded footage in the timeline and render it without a video model."""
    folder = project_path(project_id)
    manifest = load(project_id)
    if manifest["status"] == "complete" and "pending_import" not in manifest:
        return
    req = Request.model_validate(manifest["request"])
    source_name = manifest["pending_import"]["source"]
    if not re.fullmatch(r"uploads/[a-f0-9]{32}\.source", source_name):
        raise ValueError("Invalid staged upload path")
    source = folder / source_name
    scene = next(s for s in manifest["scenes"] if s["id"] == scene_id)

    def check() -> None:
        if is_cancelled():
            raise JobCancelled("Cancellation requested")

    def save(stage: str) -> None:
        manifest["stage"] = stage
        atomic_write(folder / "timeline.json", manifest)

    try:
        check()
        manifest.update(status="running", error=None)
        save(f"preparing uploaded scene {scene_id}")
        media.stream(media.inspect(source), "video")
        target_name = f"clips/scene-{scene_id:02d}-import-{source.stem}.mp4"
        target = folder / target_name
        if not target.is_file():
            partial = target.with_name(target.stem + ".partial.mp4")
            partial.unlink(missing_ok=True)
            run("ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source), "-an",
                "-vf", f"scale={req.width}:{req.height}:force_original_aspect_ratio=increase,crop={req.width}:{req.height},fps=24,setsar=1",
                "-t", str(scene["duration"]), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(partial))
            media.validate_clip(partial, scene["duration"])
            partial.replace(target)
        media.validate_clip(target, scene["duration"])
        check()
        scene["clip"] = target_name
        save("rendering imported clip")
        captions(manifest["scenes"], folder, req.voice_provider == "kokoro" and os.getenv("CAPTION_PROVIDER") == "whisper")
        render(folder, manifest)
        export_otio(folder, manifest["scenes"])
        manifest["revision"] = manifest.get("revision", 0) + 1
        manifest.pop("pending_import")
        manifest.update(status="complete", error=None)
        save("complete")
        source.unlink(missing_ok=True)
    except JobCancelled:
        manifest.update(status="cancelled", error=None)
        save("cancelled")
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save("failed")
