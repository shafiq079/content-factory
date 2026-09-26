"""Versioned project and scene contracts at the persistence boundary."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research import Source


SCHEMA_VERSION = 2


class Scene(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int = Field(ge=1)
    duration: float = Field(ge=0.3, le=3600)
    planned_duration: float | None = Field(default=None, ge=0.3, le=3600)
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    camera: str = "static"
    transition: str = "cut"
    audio_mode: Literal["narration", "native", "hybrid"] = "narration"
    generation_mode: Literal["fast", "quality"] = "fast"
    source_ids: list[int] = Field(default_factory=list)
    status: Literal["pending", "ready"] = "pending"
    start: float | None = Field(default=None, ge=0)
    clip: str | None = None
    voice: str | None = None


class Timeline(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[2]
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request: dict
    status: Literal["queued", "running", "failed", "cancelled", "complete"]
    stage: str
    scenes: list[Scene]
    assets: dict[str, str]
    error: str | None
    revision: int = Field(ge=0)
    caption_style: Literal["classic", "bold", "minimal"] = "classic"
    research: list[Source] = Field(default_factory=list)
    idea: str = ""
    hook: str = ""
    script: str = ""

    @model_validator(mode="after")
    def distinct_scenes(self):
        ids = [scene.id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scene IDs")
        return self


class Plan(BaseModel):
    idea: str = Field(min_length=3)
    scenes: list[Scene] = Field(min_length=2)


def validate_plan(plan: dict, sources: list[Source], duration: int) -> dict:
    parsed = Plan.model_validate(plan)
    ids = [scene.id for scene in parsed.scenes]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError("Planner must return ordered scenes starting at 1")
    if abs(sum(scene.duration for scene in parsed.scenes) - duration) > 0.5:
        raise ValueError("Planner scene timing differs from requested duration")
    known = {source.id for source in sources}
    if any(set(scene.source_ids) - known for scene in parsed.scenes):
        raise ValueError("Planner cited a source not in the research brief")
    scenes = [scene.model_dump(exclude_none=True) for scene in parsed.scenes]
    return {"idea": parsed.idea, "hook": scenes[0]["narration"],
            "script": " ".join(scene["narration"] for scene in scenes), "scenes": scenes}


def migrate_timeline(data: dict) -> dict:
    """Upgrade original unversioned manifests; refuse unknown future formats."""
    version = data.get("schema_version", 1)
    if version != SCHEMA_VERSION:
        if version != 1:
            raise ValueError(f"Unsupported timeline schema version: {version}")
        data = {**data, "schema_version": SCHEMA_VERSION}
    if "script" not in data:
        data = {**data, "script": " ".join(s.get("narration", "") for s in data.get("scenes", [])),
                "hook": data.get("scenes", [{}])[0].get("narration", "") if data.get("scenes") else ""}
    return Timeline.model_validate(data).model_dump()
