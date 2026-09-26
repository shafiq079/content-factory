"""Versioned project and scene contracts at the persistence boundary."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research import Conflict, ResearchBrief, Source, NUMBERS


SCHEMA_VERSION = 6


class Scene(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int = Field(ge=1)
    duration: float = Field(ge=0.3, le=3600)
    planned_duration: float | None = Field(default=None, ge=0.3, le=3600)
    narration: str = Field(min_length=1)
    visual_prompt: str = Field(min_length=1)
    camera: str = "static"
    transition: Literal["cut", "fade", "fade_white"] = "cut"
    transition_duration: float = Field(default=0.0, ge=0, le=2.0)
    beat: Literal["hook", "setup", "build", "reveal", "payoff", "cta", "ending"] = "build"
    continuity: str = ""
    audio_mode: Literal["narration", "native", "hybrid"] = "narration"
    generation_mode: Literal["fast", "quality"] = "fast"
    source_ids: list[int] = Field(default_factory=list)
    status: Literal["pending", "ready"] = "pending"
    start: float | None = Field(default=None, ge=0)
    clip: str | None = None
    voice: str | None = None
    original_clip: str | None = None
    original_voice: str | None = None
    clip_origin: Literal["generated", "uploaded"] = "generated"
    voice_origin: Literal["generated", "uploaded"] = "generated"


class MusicTrack(BaseModel):
    provider: Literal["uploaded"] = "uploaded"
    asset: str = Field(min_length=1)
    enabled: bool = True
    volume: float = Field(default=0.2, ge=0, le=1)
    loop: bool = True
    fade_in: float = Field(default=0.5, ge=0, le=30)
    fade_out: float = Field(default=3.0, ge=0, le=30)


class SFXTrack(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    provider: Literal["uploaded"] = "uploaded"
    asset: str = Field(min_length=1)
    enabled: bool = True
    start: float = Field(default=0, ge=0, le=3600)
    duration: float | None = Field(default=None, gt=0, le=3600)
    volume: float = Field(default=0.7, ge=0, le=1)
    fade_in: float = Field(default=0, ge=0, le=30)
    fade_out: float = Field(default=0.3, ge=0, le=30)


class Timeline(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[6]
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
    research_brief: ResearchBrief = Field(default_factory=ResearchBrief)
    idea: str = ""
    hook: str = ""
    script: str = ""
    story_arc: str = ""
    visual_bible: str = ""
    music: MusicTrack | None = None
    sfx: list[SFXTrack] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_scenes(self):
        ids = [scene.id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate scene IDs")
        return self


class Plan(BaseModel):
    idea: str = Field(min_length=3)
    story_arc: str = ""
    visual_bible: str = ""
    scenes: list[Scene] = Field(min_length=2)


def validate_plan(plan: dict, sources: list[Source], duration: int,
                  conflicts: list[Conflict] | None = None) -> dict:
    parsed = Plan.model_validate(plan)
    ids = [scene.id for scene in parsed.scenes]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError("Planner must return ordered scenes starting at 1")
    if abs(sum(scene.duration for scene in parsed.scenes) - duration) > 0.5:
        raise ValueError("Planner scene timing differs from requested duration")
    if any(scene.duration < 3 or scene.duration > 8 for scene in parsed.scenes):
        raise ValueError("Planner scenes must be between 3 and 8 seconds")
    if parsed.scenes[0].beat != "hook":
        raise ValueError("First scene must use the hook beat")
    if parsed.scenes[-1].beat not in ("cta", "ending"):
        raise ValueError("Last scene must use the cta or ending beat")
    for scene in parsed.scenes:
        words = len(scene.narration.split())
        if words > math.ceil(scene.duration * 2.6):
            raise ValueError(f"Scene {scene.id} narration is too long for its duration")
    known = {source.id for source in sources}
    if any(set(scene.source_ids) - known for scene in parsed.scenes):
        raise ValueError("Planner cited a source not in the research brief")
    by_id = {source.id: source for source in sources}
    for scene in parsed.scenes:
        figures = {token.replace(",", "") for token in NUMBERS.findall(scene.narration)}
        if sources and figures:
            if not scene.source_ids:
                raise ValueError(f"Scene {scene.id} has a precise figure without a source ID")
            evidence = " ".join((by_id[source_id].evidence or by_id[source_id].excerpt) for source_id in scene.source_ids)
            available = {token.replace(",", "") for token in NUMBERS.findall(evidence)}
            if figures - available:
                raise ValueError(f"Scene {scene.id} has a figure absent from its cited evidence")
            if any(set(conflict.source_ids) & set(scene.source_ids) for conflict in conflicts or []):
                if not any(word in scene.narration.lower() for word in ("according", "estimat", "reported", "between", "varies", "disagree", "differ", "around", "approximately", "about")):
                    raise ValueError(f"Scene {scene.id} presents a disputed figure without qualification")
    scenes = [scene.model_dump(exclude_none=True) for scene in parsed.scenes]
    return {"idea": parsed.idea, "story_arc": parsed.story_arc, "visual_bible": parsed.visual_bible,
            "hook": scenes[0]["narration"],
            "script": " ".join(scene["narration"] for scene in scenes), "scenes": scenes}


def migrate_timeline(data: dict) -> dict:
    """Upgrade older manifests in place; refuse unknown future formats."""
    version = data.get("schema_version", 1)
    if version not in (1, 2, 3, 4, 5, SCHEMA_VERSION):
        raise ValueError(f"Unsupported timeline schema version: {version}")
    if version != SCHEMA_VERSION:
        data = {**data, "schema_version": SCHEMA_VERSION}
    if "script" not in data:
        data = {**data, "script": " ".join(s.get("narration", "") for s in data.get("scenes", [])),
                "hook": data.get("scenes", [{}])[0].get("narration", "") if data.get("scenes") else ""}
    scenes = []
    for index, scene in enumerate(data.get("scenes", [])):
        item = dict(scene)
        item.setdefault("original_clip", item.get("clip"))
        item.setdefault("original_voice", item.get("voice"))
        item.setdefault("clip_origin", "generated")
        item.setdefault("voice_origin", "generated")
        transition = item.get("transition", "cut")
        if transition not in ("cut", "fade", "fade_white"):
            transition = "cut"
        if index == 0:
            transition = "cut"
        item["transition"] = transition
        item.setdefault("transition_duration", 0.0 if transition == "cut" else 0.8)
        if transition == "cut":
            item["transition_duration"] = 0.0
        scenes.append(item)
    data = {**data, "scenes": scenes}
    data.setdefault("music", None)
    data.setdefault("sfx", [])
    data.setdefault("research_brief", ResearchBrief(mode="legacy" if data.get("research") else "none").model_dump())
    return Timeline.model_validate(data).model_dump()
