"""Persistent single-host job queue backed by SQLite.

Workers claim jobs in a transaction and renew a short lease. This supports
process restarts and multiple API processes sharing one local project volume.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import contracts, core


LEASE_SECONDS = 60


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "jobs.sqlite3"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                project_id TEXT PRIMARY KEY, kind TEXT NOT NULL, scene_id INTEGER,
                state TEXT NOT NULL, token TEXT, lease_until REAL,
                attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )""")
            # Recover projects created just before a crash interrupted insertion.
            for item in self.root.glob("*/timeline.json"):
                try:
                    manifest = core.load(item.parent.name)
                except (ValueError, OSError):
                    continue
                if manifest.get("status") in ("queued", "running"):
                    pending = manifest.get("pending_job") or {}
                    kind = pending.get("kind", "generate")
                    if kind not in {"generate", "regenerate", "render", "timeline", "revoice", "narration"}:
                        continue
                    db.execute("""INSERT INTO jobs(project_id,kind,scene_id,state,updated_at) VALUES(?,?,?,?,?)
                        ON CONFLICT(project_id) DO UPDATE SET kind=excluded.kind,scene_id=excluded.scene_id,
                        state='queued',token=NULL,lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at
                        WHERE jobs.state NOT IN ('queued','running')""",
                        (item.parent.name, kind, pending.get("scene_id"), "queued", time.time()))

    def enqueue_generate(self, project_id: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO jobs(project_id,kind,state,updated_at) VALUES(?,?,?,?)",
                       (project_id, "generate", "queued", time.time()))

    def enqueue_regeneration(self, project_id: str, scene_id: int, visual_prompt: str | None,
                             narration: str | None, audio_mode: str | None = None) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can regenerate scenes")
            scene = next((s for s in manifest["scenes"] if s["id"] == scene_id), None)
            if scene is None:
                raise ValueError("Unknown scene")
            if audio_mode is not None and audio_mode not in ("narration", "native", "hybrid"):
                raise ValueError("Unknown audio mode")
            if visual_prompt is not None:
                scene["visual_prompt"] = visual_prompt
            if narration is not None:
                scene["narration"] = narration
                manifest["script"] = " ".join(s["narration"] for s in manifest["scenes"])
                manifest["hook"] = manifest["scenes"][0]["narration"]
            if audio_mode is not None:
                scene["audio_mode"] = audio_mode
                if audio_mode == "native":
                    scene["voice"] = None
            scene["status"] = "pending"
            # New generation uses new paths; existing source files remain recoverable.
            scene["clip"] = f"clips/scene-{scene_id:02d}-{uuid.uuid4().hex}.mp4"
            scene["clip_origin"] = "generated"
            if (narration is not None or audio_mode is not None) and scene["audio_mode"] != "native":
                scene["voice"] = f"voice/scene-{scene_id:02d}-{uuid.uuid4().hex}.wav"
                scene["voice_origin"] = "generated"
            manifest.update(status="queued", stage=f"queued to regenerate scene {scene_id}", error=None)
            manifest["pending_job"] = {"kind": "regenerate", "scene_id": scene_id}
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,scene_id,state,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                kind=excluded.kind,scene_id=excluded.scene_id,state='queued',token=NULL,
                lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, "regenerate", scene_id, "queued", time.time()))
            return manifest

    def enqueue_scene_asset(self, project_id: str, scene_id: int, asset: str, path: str,
                            duration: float | None = None, narration: str | None = None) -> dict:
        """Activate an imported asset and rerender without a video model."""
        def edit(manifest: dict, scene: dict) -> str:
            if asset == "clip":
                if scene.get("audio_mode") == "native" and not core.media.has_audio(core.project_path(project_id) / path):
                    raise ValueError("Native audio mode requires a video with an audio track")
                scene["clip"] = path
                scene["clip_origin"] = "uploaded"
                return "timeline"
            if scene.get("audio_mode", "narration") == "native":
                raise ValueError("Native audio is embedded in the clip; switch to narration or hybrid before replacing narration")
            scene["original_voice"] = scene.get("original_voice") or scene.get("voice")
            scene["voice"] = path
            scene["voice_origin"] = "uploaded"
            scene["duration"] = duration
            if narration is not None:
                scene["narration"] = narration
            core.recalculate_timeline(manifest)
            return "timeline"
        return self._enqueue_scene_edit(project_id, scene_id, "queued to replace scene asset", edit)

    def enqueue_scene_narration(self, project_id: str, scene_id: int, narration: str) -> dict:
        def edit(manifest: dict, scene: dict) -> str:
            if scene.get("audio_mode", "narration") == "native":
                raise ValueError("Native dialogue is embedded in the video. Change audio mode first, or regenerate the video with new dialogue")
            scene["narration"] = narration
            scene["voice"] = f"voice/scene-{scene_id:02d}-{uuid.uuid4().hex}.wav"
            scene["voice_origin"] = "generated"
            core.recalculate_timeline(manifest)
            return "narration"
        return self._enqueue_scene_edit(project_id, scene_id, "queued to regenerate scene narration", edit)

    def enqueue_scene_audio_mode(self, project_id: str, scene_id: int, mode: str) -> dict:
        def edit(manifest: dict, scene: dict) -> str:
            folder = core.project_path(project_id)
            if mode == "native":
                if not core.media.has_audio(folder / scene["clip"]):
                    raise ValueError("Native audio requires an active clip with an audio track")
            elif not scene.get("voice"):
                scene["voice"] = f"voice/scene-{scene_id:02d}-{uuid.uuid4().hex}.wav"
                scene["voice_origin"] = "generated"
                scene["audio_mode"] = mode
                return "narration"
            else:
                measured = core.media.validate_voice(folder / scene["voice"], scene["duration"], False)
                scene["duration"] = round(measured.duration, 3)
            scene["audio_mode"] = mode
            core.recalculate_timeline(manifest)
            return "timeline"
        return self._enqueue_scene_edit(project_id, scene_id, "queued to change scene audio mode", edit)

    def _enqueue_scene_edit(self, project_id: str, scene_id: int, stage: str,
                            edit: Callable[[dict, dict], str]) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can edit scenes")
            scene = next((s for s in manifest["scenes"] if s["id"] == scene_id), None)
            if scene is None:
                raise ValueError("Unknown scene")
            kind = edit(manifest, scene)
            manifest["pending_job"] = {"kind": kind, "scene_id": scene_id}
            manifest.update(status="queued", stage=stage, error=None)
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,scene_id,state,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET kind=excluded.kind,scene_id=excluded.scene_id,
                state='queued',token=NULL,lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, kind, scene_id, "queued", time.time()))
            return manifest

    def enqueue_render(self, project_id: str, caption_style: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can render again")
            if caption_style not in core.CAPTION_STYLES:
                raise ValueError("Unknown caption style")
            manifest.update(caption_style=caption_style, status="queued", stage="queued to render again", error=None)
            manifest["pending_job"] = {"kind": "render"}
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,state,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET kind='render',scene_id=NULL,state='queued',token=NULL,
                lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, "render", "queued", time.time()))
            return manifest

    def _enqueue_audio_edit(self, project_id: str, stage: str,
                            edit: Callable[[dict], None]) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can edit music or SFX")
            edit(manifest)
            manifest.update(status="queued", stage=stage, error=None)
            manifest["pending_job"] = {"kind": "render"}
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,state,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET kind='render',scene_id=NULL,state='queued',token=NULL,
                lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, "render", "queued", time.time()))
            return manifest

    def enqueue_music(self, project_id: str, music: dict) -> dict:
        validated = contracts.MusicTrack.model_validate(music).model_dump()
        def edit(manifest: dict) -> None:
            manifest["music"] = validated
        return self._enqueue_audio_edit(project_id, "queued to apply background music", edit)

    def update_music(self, project_id: str, settings: dict) -> dict:
        def edit(manifest: dict) -> None:
            current = manifest.get("music")
            if not current:
                raise ValueError("Project has no background music")
            manifest["music"] = contracts.MusicTrack.model_validate({**current, **settings}).model_dump()
        return self._enqueue_audio_edit(project_id, "queued to update background music", edit)

    def remove_music(self, project_id: str) -> dict:
        def edit(manifest: dict) -> None:
            manifest["music"] = None
        return self._enqueue_audio_edit(project_id, "queued to remove background music", edit)

    def enqueue_sfx(self, project_id: str, effect: dict) -> dict:
        validated = contracts.SFXTrack.model_validate(effect).model_dump()
        def edit(manifest: dict) -> None:
            manifest.setdefault("sfx", []).append(validated)
        return self._enqueue_audio_edit(project_id, "queued to add sound effect", edit)

    def remove_sfx(self, project_id: str, effect_id: str) -> dict:
        def edit(manifest: dict) -> None:
            items = manifest.get("sfx", [])
            if not any(item.get("id") == effect_id for item in items):
                raise ValueError("Unknown sound effect")
            manifest["sfx"] = [item for item in items if item.get("id") != effect_id]
        return self._enqueue_audio_edit(project_id, "queued to remove sound effect", edit)


    def enqueue_timeline_edit(self, project_id: str, scene_order: list[int] | None,
                              transitions: list[dict]) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can edit the timeline")

            by_id = {scene["id"]: scene for scene in manifest["scenes"]}
            if scene_order is not None:
                if len(scene_order) != len(by_id) or len(set(scene_order)) != len(scene_order) or set(scene_order) != set(by_id):
                    raise ValueError("Scene order must contain every scene exactly once")
                manifest["scenes"] = [by_id[scene_id] for scene_id in scene_order]

            for edit in transitions:
                scene_id = edit["scene_id"]
                scene = by_id.get(scene_id)
                if scene is None:
                    raise ValueError(f"Unknown scene: {scene_id}")
                transition = edit["transition"]
                duration = float(edit["transition_duration"])
                if transition not in core.TRANSITIONS:
                    raise ValueError("Unknown transition")
                if transition == "cut":
                    duration = 0.0
                elif not 0.2 <= duration <= 2.0:
                    raise ValueError("Fade transition duration must be between 0.2 and 2 seconds")
                scene["transition"] = transition
                scene["transition_duration"] = duration

            core.recalculate_timeline(manifest)
            manifest.update(status="queued", stage="queued to update timeline", error=None)
            manifest["pending_job"] = {"kind": "timeline"}
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,state,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET kind='timeline',scene_id=NULL,state='queued',token=NULL,
                lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, "timeline", "queued", time.time()))
            return manifest


    def enqueue_revoice(self, project_id: str, voice_id: str, voice_speed: float) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row and row["state"] in ("queued", "running"):
                raise RuntimeError("Project already has a running or queued job")
            manifest = core.load(project_id)
            if manifest["status"] != "complete":
                raise RuntimeError("Only completed projects can change narration voice")
            request = core.Request.model_validate({
                **manifest["request"],
                "voice_id": voice_id,
                "voice_speed": voice_speed,
            })
            if request.voice_provider != "kokoro":
                raise RuntimeError("Reusable voice controls currently require Kokoro narration")
            # Store the resolved voice so later retries/regeneration are reproducible
            # even if machine environment variables change.
            resolved = core.resolve_kokoro_voice(request.language, request.voice_id)
            request = request.model_copy(update={"voice_id": resolved})
            manifest["request"] = request.model_dump()
            manifest.update(status="queued", stage="queued to regenerate narration", error=None)
            manifest["pending_job"] = {"kind": "revoice"}
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("""INSERT INTO jobs(project_id,kind,state,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET kind='revoice',scene_id=NULL,state='queued',token=NULL,
                lease_until=NULL,cancel_requested=0,updated_at=excluded.updated_at""",
                (project_id, "revoice", "queued", time.time()))
            return manifest


    def claim(self) -> dict | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE jobs SET state='queued',token=NULL,lease_until=NULL,updated_at=?
                WHERE state='running' AND lease_until<?""", (now, now))
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY updated_at LIMIT 1").fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("""UPDATE jobs SET state='running',token=?,lease_until=?,attempts=attempts+1,
                updated_at=? WHERE project_id=?""", (token, now + LEASE_SECONDS, now, row["project_id"]))
            return {**dict(row), "token": token}

    def heartbeat(self, project_id: str, token: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET lease_until=?,updated_at=? WHERE project_id=? AND token=? AND state='running'",
                       (time.time() + LEASE_SECONDS, time.time(), project_id, token))

    def cancelled(self, project_id: str, token: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT cancel_requested,token FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            return row is None or row["token"] != token or bool(row["cancel_requested"])

    def finish(self, project_id: str, token: str, state: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE jobs SET state=?,token=NULL,lease_until=NULL,updated_at=? WHERE project_id=? AND token=?",
                       (state, time.time(), project_id, token))

    def cancel(self, project_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row is None or row["state"] not in ("queued", "running"):
                raise RuntimeError("No queued or running job to cancel")
            manifest = core.load(project_id)
            if row["state"] == "queued":
                db.execute("UPDATE jobs SET state='cancelled',cancel_requested=1,updated_at=? WHERE project_id=?", (time.time(), project_id))
                manifest.update(status="cancelled", stage="cancelled", error=None)
            else:
                db.execute("UPDATE jobs SET cancel_requested=1,updated_at=? WHERE project_id=?", (time.time(), project_id))
                manifest["stage"] = "cancelling after current step"
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            return manifest

    def retry(self, project_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE project_id=?", (project_id,)).fetchone()
            if row is None or row["state"] not in ("failed", "cancelled"):
                raise RuntimeError("Only failed or cancelled jobs can be retried")
            manifest = core.load(project_id)
            manifest.update(status="queued", stage="queued to retry", error=None)
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            db.execute("UPDATE jobs SET state='queued',token=NULL,lease_until=NULL,cancel_requested=0,updated_at=? WHERE project_id=?",
                       (time.time(), project_id))
            return manifest


def worker_loop(store: JobStore, stop: threading.Event) -> None:
    while not stop.is_set():
        job = store.claim()
        if job is None:
            stop.wait(0.5)
            continue
        project_id, token = job["project_id"], job["token"]
        heartbeat_stop = threading.Event()

        def keep_lease() -> None:
            while not heartbeat_stop.wait(10):
                store.heartbeat(project_id, token)

        heartbeat_thread = threading.Thread(target=keep_lease, daemon=True)
        heartbeat_thread.start()
        try:
            check = lambda: store.cancelled(project_id, token)
            if job["kind"] == "regenerate":
                core.regenerate_work(project_id, job["scene_id"], check)
            elif job["kind"] == "narration":
                core.scene_narration_work(project_id, job["scene_id"], check)
            elif job["kind"] == "render":
                core.render_work(project_id, check)
            elif job["kind"] == "revoice":
                core.revoice_work(project_id, check)
            elif job["kind"] == "timeline":
                core.timeline_work(project_id, check)
            else:
                core.process(project_id, check)
            state = core.load(project_id)["status"]
            store.finish(project_id, token, state)
        except Exception as exc:
            manifest = core.load(project_id)
            manifest.update(status="failed", stage="failed", error=f"{type(exc).__name__}: {exc}")
            core.atomic_write(core.project_path(project_id) / "timeline.json", manifest)
            store.finish(project_id, token, "failed")
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
