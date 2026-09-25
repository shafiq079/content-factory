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

from . import core


@dataclass(frozen=True)
class Adapter:
    factory: Callable
    check: Callable[[core.Request], None]


def ready(_: core.Request) -> None:
    pass


def dependency(name: str) -> None:
    if importlib.util.find_spec(name) is None:
        raise RuntimeError(f"Provider dependency missing: {name}; install requirements-ai.txt on the worker")


def ltx_check(_: core.Request) -> None:
    config = Path(os.getenv("LTX_CONFIG", "ltx-models.json"))
    if not config.is_file():
        raise RuntimeError(f"LTX_CONFIG missing: {config}. See ltx-models.example.json")
    try:
        paths = json.loads(config.read_text(encoding="utf-8"))
        for name in core.LTX25Video.KEYS:
            path = Path(paths[name]).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"Missing LTX checkpoint: {name}: {path}")
    except (ValueError, KeyError) as exc:
        raise RuntimeError(f"Invalid LTX_CONFIG: {exc}") from exc
    dependency("ltx_pipelines")


def kokoro_check(request: core.Request) -> None:
    if request.language.lower() not in ("english", "british english", "spanish", "french", "hindi", "italian", "japanese", "portuguese", "chinese"):
        raise RuntimeError(f"Kokoro language not configured: {request.language}")
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
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    if model not in (item.get("name") for item in models):
        raise RuntimeError(f"Ollama model not installed: {model}")


def whisper_check(_: core.Request) -> None:
    dependency("faster_whisper")


REGISTRY: dict[str, dict[str, Adapter]] = {
    "planner": {"template": Adapter(core.TemplatePlanner, ready), "ollama": Adapter(core.OllamaPlanner, ollama_check)},
    "video": {"preview": Adapter(core.PreviewVideo, ready), "ltx25": Adapter(core.LTX25Video, ltx_check)},
    "voice": {"silent": Adapter(core.SilentVoice, ready), "kokoro": Adapter(core.KokoroVoice, kokoro_check)},
    "captions": {"script": Adapter(lambda: None, ready), "whisper": Adapter(lambda: None, whisper_check)},
}


def caption_choice(request: core.Request) -> str:
    return "whisper" if request.voice_provider == "kokoro" and os.getenv("CAPTION_PROVIDER") == "whisper" else "script"


def preflight(request: core.Request) -> None:
    selections = {"planner": request.planner_provider, "video": request.video_provider,
                  "voice": request.voice_provider, "captions": caption_choice(request)}
    for kind, name in selections.items():
        try:
            adapter = REGISTRY[kind][name]
        except KeyError as exc:
            raise RuntimeError(f"Unknown {kind} provider: {name}") from exc
        adapter.check(request)


def make(kind: str, name: str):
    return REGISTRY[kind][name].factory()
