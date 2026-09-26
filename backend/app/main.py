from contextlib import asynccontextmanager
import threading
import uuid
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import audio, contracts, core, providers
from .core import Request, create, load, project_path
from .jobs import JobStore, worker_loop


@asynccontextmanager
async def lifespan(application: FastAPI):
    store = JobStore(core.ROOT)
    store.initialize()
    stop = threading.Event()
    worker = threading.Thread(target=worker_loop, args=(store, stop), daemon=True)
    application.state.jobs = store
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)


app = FastAPI(title="Content Factory", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/projects", status_code=202)
def new_project(request: Request):
    try:
        providers.preflight(request)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    project = create(request)
    app.state.jobs.enqueue_generate(project["id"])
    return project


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    try:
        return load(project_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project not found")


class SceneEdit(BaseModel):
    visual_prompt: str | None = Field(None, min_length=3, max_length=3000)
    narration: str | None = Field(None, min_length=2, max_length=2000)
    audio_mode: Literal["narration", "native", "hybrid"] | None = None


class RenderEdit(BaseModel):
    caption_style: Literal["classic", "bold", "minimal"] = "classic"


class VoiceEdit(BaseModel):
    voice_id: str = Field(default="", max_length=200)
    voice_speed: float = Field(default=1.0, ge=0.5, le=2.0)


class MusicEdit(BaseModel):
    enabled: bool = True
    volume: float = Field(default=0.2, ge=0, le=1)
    loop: bool = True
    fade_in: float = Field(default=0.5, ge=0, le=30)
    fade_out: float = Field(default=3.0, ge=0, le=30)


@app.post("/projects/{project_id}/scenes/{scene_id}/regenerate", status_code=202)
def regenerate_scene(project_id: str, scene_id: int, edit: SceneEdit):
    try:
        providers.preflight(Request.model_validate(load(project_id)["request"]))
        return app.state.jobs.enqueue_regeneration(project_id, scene_id, edit.visual_prompt, edit.narration, edit.audio_mode)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project or scene not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/projects/{project_id}/voice", status_code=202)
def change_project_voice(project_id: str, edit: VoiceEdit):
    try:
        manifest = load(project_id)
        request = Request.model_validate({
            **manifest["request"],
            "voice_id": edit.voice_id,
            "voice_speed": edit.voice_speed,
        })
        providers.preflight_voice(request)
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return app.state.jobs.enqueue_revoice(project_id, edit.voice_id, edit.voice_speed)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/music", status_code=202)
def upload_project_music(
    project_id: str,
    file: UploadFile = File(...),
    volume: float = Form(0.2),
    loop: bool = Form(True),
    fade_in: float = Form(0.5),
    fade_out: float = Form(3.0),
):
    relative_path = None
    try:
        folder = project_path(project_id)
        manifest = load(project_id)
        if manifest["status"] != "complete":
            raise RuntimeError("Only completed projects can edit music or SFX")
        old_asset = (manifest.get("music") or {}).get("asset")
        relative_path, _ = audio.save_upload(folder, "music", file.filename or "", file.file)
        music = contracts.MusicTrack.model_validate({
            "provider": "uploaded",
            "asset": relative_path,
            "enabled": True,
            "volume": volume,
            "loop": loop,
            "fade_in": fade_in,
            "fade_out": fade_out,
        }).model_dump()
        result = app.state.jobs.enqueue_music(project_id, music)
        if old_asset and old_asset != relative_path:
            audio.remove_asset(folder, old_asset)
        return result
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except (audio.AudioAssetError, ValueError) as exc:
        if relative_path:
            audio.remove_asset(project_path(project_id), relative_path)
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        if relative_path:
            audio.remove_asset(project_path(project_id), relative_path)
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/music/settings", status_code=202)
def update_project_music(project_id: str, edit: MusicEdit):
    try:
        return app.state.jobs.update_music(project_id, edit.model_dump())
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/music/remove", status_code=202)
def remove_project_music(project_id: str):
    try:
        manifest = load(project_id)
        old_asset = (manifest.get("music") or {}).get("asset")
        result = app.state.jobs.remove_music(project_id)
        audio.remove_asset(project_path(project_id), old_asset)
        return result
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/sfx", status_code=202)
def upload_project_sfx(
    project_id: str,
    file: UploadFile = File(...),
    start: float = Form(0.0),
    volume: float = Form(0.7),
    fade_in: float = Form(0.0),
    fade_out: float = Form(0.3),
):
    relative_path = None
    try:
        folder = project_path(project_id)
        manifest = load(project_id)
        if manifest["status"] != "complete":
            raise RuntimeError("Only completed projects can edit music or SFX")
        total = float(manifest.get("duration_actual") or sum(scene["duration"] for scene in manifest["scenes"]))
        if start < 0 or start >= total:
            raise ValueError(f"SFX start must be within the {total:.2f}-second timeline")
        relative_path, source_duration = audio.save_upload(folder, "sfx", file.filename or "", file.file)
        effect = contracts.SFXTrack.model_validate({
            "id": uuid.uuid4().hex,
            "provider": "uploaded",
            "asset": relative_path,
            "enabled": True,
            "start": start,
            "duration": min(source_duration, total - start),
            "volume": volume,
            "fade_in": fade_in,
            "fade_out": fade_out,
        }).model_dump()
        return app.state.jobs.enqueue_sfx(project_id, effect)
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except (audio.AudioAssetError, ValueError) as exc:
        if relative_path:
            audio.remove_asset(project_path(project_id), relative_path)
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        if relative_path:
            audio.remove_asset(project_path(project_id), relative_path)
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/sfx/{effect_id}/remove", status_code=202)
def remove_project_sfx(project_id: str, effect_id: str):
    try:
        manifest = load(project_id)
        effect = next((item for item in manifest.get("sfx", []) if item.get("id") == effect_id), None)
        if effect is None:
            raise ValueError("Unknown sound effect")
        result = app.state.jobs.remove_sfx(project_id, effect_id)
        audio.remove_asset(project_path(project_id), effect.get("asset"))
        return result
    except FileNotFoundError:
        raise HTTPException(404, "Project not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/render", status_code=202)
def render_project(project_id: str, edit: RenderEdit):
    try:
        return app.state.jobs.enqueue_render(project_id, edit.caption_style)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/projects/{project_id}/cancel", status_code=202)
def cancel_project(project_id: str):
    try:
        return app.state.jobs.cancel(project_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/projects/{project_id}/retry", status_code=202)
def retry_project(project_id: str):
    try:
        return app.state.jobs.retry(project_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/projects/{project_id}/assets/{asset_path:path}")
def asset(project_id: str, asset_path: str):
    try:
        folder = project_path(project_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Project not found")
    target = (folder / asset_path).resolve()
    if not target.is_relative_to(folder) or not target.is_file() or target.suffix.lower() not in (".mp4", ".srt", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".json", ".otio"):
        raise HTTPException(404, "Asset not found")
    return FileResponse(target)
