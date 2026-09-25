"""Versioned project and scene contracts at the persistence boundary."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = 2


class Scene(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int = Field(ge=1)
    duration: float = Field(ge=0.3, le=3600)
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    camera: str = "static"
    transition: str = "cut"
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

    @model_validator(mode="after")
    def distinct_scenes(self):
        ids = [scene.id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scene IDs")
        return self


def validate_scenes(scenes: list[dict]) -> list[dict]:
    parsed = [Scene.model_validate(scene).model_dump(exclude_none=True) for scene in scenes]
    if not parsed or len({scene["id"] for scene in parsed}) != len(parsed):
        raise ValueError("Planner must return distinct scenes")
    return parsed


def migrate_timeline(data: dict) -> dict:
    """Upgrade original unversioned manifests; refuse unknown future formats."""
    version = data.get("schema_version", 1)
    if version != SCHEMA_VERSION:
        if version != 1:
            raise ValueError(f"Unsupported timeline schema version: {version}")
        data = {**data, "schema_version": SCHEMA_VERSION}
    return Timeline.model_validate(data).model_dump()
