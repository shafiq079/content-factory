"""Adapter registry and checks that run before a job uses an adapter."""
from __future__ import annotations

import importlib.util
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import core, research
from .wan_video import Wan22Video


@dataclass(frozen=True)
class Adapter:
    factory: Callable
    check: Callable[[core.Request], None]


def ready(_: core.Request) -> None:
    pass


def dependency(name: str) -> None:
    if importlib.util.find_spec(name) is None:
        raise RuntimeError(f"Provider dependency missing: {name}; install requirements-ai.txt on the worker")


def ltx_check(request: core.Request) -> None:
    core.LTX25Video.configured_paths(request.generation_mode)
    dependency("ltx_pipelines")
    dependency("ltx_core")


def kokoro_check(request: core.Request) -> None:
    try:
        core.resolve_kokoro_voice(request.language, request.voice_id)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for package in ("kokoro", "soundfile", "numpy"):
        dependency(package)


def ollama_check(_: core.Request) -> None:
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("OLLAMA_URL must refer to a local HTTP service")
    try:
        with urllib.request.urlopen(f"{parsed.scheme}://{parsed.netloc}/api/tags", timeout=2) as response:
            models = json.load(response).get("models", [])
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Ollama is unavailable at {parsed.netloc}: {exc}") from exc
    installed = {item.get("name") for item in models}
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    if model not in installed:
        raise RuntimeError(f"Ollama model not installed: {model}")
    review_model = os.getenv("OLLAMA_REVIEW_MODEL", model)
    if review_model not in installed:
        raise RuntimeError(f"Ollama review model not installed: {review_model}")


def whisper_check(_: core.Request) -> None:
    dependency("faster_whisper")


REGISTRY: dict[str, dict[str, Adapter]] = {
    "research": {"none": Adapter(research.NoResearch, ready), "wikipedia": Adapter(research.WikipediaResearch, ready),
                 "broader": Adapter(research.BroaderResearch, ready)},
    "planner": {"template": Adapter(core.TemplatePlanner, ready), "ollama": Adapter(core.OllamaPlanner, ollama_check)},
    "video": {"preview": Adapter(core.PreviewVideo, ready), "ltx25": Adapter(core.LTX25Video, ltx_check),
              "wan22": Adapter(Wan22Video, Wan22Video.preflight)},
    "voice": {"silent": Adapter(core.SilentVoice, ready), "kokoro": Adapter(core.KokoroVoice, kokoro_check)},
    "captions": {"script": Adapter(lambda: None, ready), "whisper": Adapter(lambda: None, whisper_check)},
}


def research_choice(request: core.Request) -> str:
    if request.research_provider == "auto":
        return "broader" if request.planner_provider == "ollama" else "none"
    return request.research_provider


def caption_choice(request: core.Request) -> str:
    return "whisper" if request.voice_provider == "kokoro" and os.getenv("CAPTION_PROVIDER") == "whisper" else "script"


def preflight(request: core.Request) -> None:
    selections = {"research": research_choice(request), "planner": request.planner_provider, "video": request.video_provider,
                  "voice": request.voice_provider, "captions": caption_choice(request)}
    for kind, name in selections.items():
        try:
            adapter = REGISTRY[kind][name]
        except KeyError as exc:
            raise RuntimeError(f"Unknown {kind} provider: {name}") from exc
        adapter.check(request)


def preflight_voice(request: core.Request) -> None:
    """Check only dependencies needed to regenerate narration/captions, not the GPU video stack."""
    selections = {"voice": request.voice_provider, "captions": caption_choice(request)}
    for kind, name in selections.items():
        try:
            adapter = REGISTRY[kind][name]
        except KeyError as exc:
            raise RuntimeError(f"Unknown {kind} provider: {name}") from exc
        adapter.check(request)


def make(kind: str, name: str):
    return REGISTRY[kind][name].factory()
