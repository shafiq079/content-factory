from contextlib import asynccontextmanager
import threading
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import core, providers
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
        providers.preflight(request)
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
    if not target.is_relative_to(folder) or not target.is_file() or target.suffix.lower() not in (".mp4", ".srt", ".wav", ".json", ".otio"):
        raise HTTPException(404, "Asset not found")
    return FileResponse(target)
