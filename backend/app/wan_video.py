"""Optional official Wan 2.2 TI2V-5B text-to-video adapter.

The operator supplies a local Wan checkout and checkpoints. Nothing from this
module imports the GPU stack on the CPU preview path.
"""
from __future__ import annotations

import gc
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

from . import core, media


class Wan22Video(core.VideoGenerator):
    _runtime = None
    _runtime_key: tuple[str, str, int] | None = None
    _runtime_lock = threading.Lock()

    @staticmethod
    def configured_paths() -> tuple[Path, Path]:
        repo_setting = os.getenv("WAN22_REPO")
        model_setting = os.getenv("WAN22_CHECKPOINT")
        if not repo_setting or not model_setting:
            raise RuntimeError("Wan requires WAN22_REPO and WAN22_CHECKPOINT; see README.md")
        repo = Path(repo_setting).expanduser().resolve()
        model = Path(model_setting).expanduser().resolve()
        if not (repo / "wan" / "textimage2video.py").is_file():
            raise RuntimeError(f"WAN22_REPO must point to the official Wan2.2 checkout: {repo}")
        for name in ("config.json", "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.2_VAE.pth",
                     "diffusion_pytorch_model.safetensors.index.json"):
            if not (model / name).is_file():
                raise RuntimeError(f"Missing Wan TI2V-5B checkpoint file: {model / name}")
        try:
            index = json.loads((model / "diffusion_pytorch_model.safetensors.index.json").read_text())
            shards = set(index["weight_map"].values())
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError(f"Invalid Wan checkpoint index: {exc}") from exc
        if not shards or any(Path(shard).name != shard or not (model / shard).is_file() for shard in shards):
            raise RuntimeError("Wan checkpoint index references missing or unsafe model shards")
        if not (model / "google" / "umt5-xxl").is_dir():
            raise RuntimeError(f"Missing Wan tokenizer directory: {model / 'google/umt5-xxl'}")
        return repo, model

    @staticmethod
    def preflight(request: core.Request) -> None:
        if request.generation_mode != "fast":
            raise RuntimeError("Wan TI2V-5B uses its standard sampler; LTX Quality is unavailable")
        Wan22Video.configured_paths()
        for name in ("torch", "torchvision", "imageio", "easydict"):
            if importlib.util.find_spec(name) is None:
                raise RuntimeError(f"Wan dependency missing: {name}; install the official Wan2.2 requirements on the worker")
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("Wan TI2V-5B requires a CUDA GPU; use Preview for CPU-only projects")

    @staticmethod
    def _build_runtime(repo: Path, model: Path, device_id: int):
        # The upstream checkout provides `wan` as an importable Python package.
        # Do not copy its inference code or pull model files during a job.
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import wan
        if not Path(wan.__file__).resolve().is_relative_to(repo):
            raise RuntimeError("A different 'wan' Python package is already loaded; restart the worker")
        from wan.configs import WAN_CONFIGS
        from wan.utils.utils import save_video
        cfg = WAN_CONFIGS["ti2v-5B"]
        pipeline = wan.WanTI2V(config=cfg, checkpoint_dir=str(model), device_id=device_id,
                               t5_cpu=True, convert_model_dtype=True)
        return pipeline, cfg, save_video

    @classmethod
    def _runtime_for(cls, repo: Path, model: Path, device_id: int):
        key = (str(repo), str(model), device_id)
        with cls._runtime_lock:
            if cls._runtime is None or cls._runtime_key != key:
                had_runtime = cls._runtime is not None
                cls._runtime = None
                cls._runtime_key = None
                if had_runtime:
                    gc.collect()
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                cls._runtime = cls._build_runtime(repo, model, device_id)
                cls._runtime_key = key
            return cls._runtime

    @classmethod
    def release_runtime(cls) -> None:
        with cls._runtime_lock:
            if cls._runtime is not None:
                cls._runtime = None
                cls._runtime_key = None
                gc.collect()
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

    def generate(self, scene: dict, output: Path, request: core.Request) -> None:
        if scene.get("audio_mode", "narration") != "narration":
            raise RuntimeError("Wan TI2V-5B generates video without native audio; use narration mode")
        repo, model = self.configured_paths()
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("Wan TI2V-5B requires a CUDA GPU")
        with core.GPU_VIDEO_LOCK:
            core.LTX25Video.release_runtime()
            pipeline, cfg, save_video = self._runtime_for(repo, model, torch.cuda.current_device())
            # Upstream requires 4n+1 frames at 24 fps. Limit inference to its
            # documented 121-frame default; stretch the clip to longer scenes
            # instead of allocating unbounded GPU memory.
            frames = min(121, max(9, 4 * round((float(scene["duration"]) * 24 - 1) / 4) + 1))
            size = (704, 1280) if request.height >= request.width else (1280, 704)
            raw = output.with_name(output.stem + ".wan-raw.mp4")
            raw.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            try:
                with torch.inference_mode():
                    video = pipeline.generate(
                        input_prompt=scene["visual_prompt"], size=size, frame_num=frames,
                        shift=cfg.sample_shift, sampling_steps=cfg.sample_steps,
                        guide_scale=cfg.sample_guide_scale, seed=int(os.getenv("WAN22_SEED", "42")) + scene["id"] - 1,
                        offload_model=True,
                    )
                    try:
                        save_video(tensor=video[None], save_file=str(raw), fps=cfg.sample_fps,
                                   nrow=1, normalize=True, value_range=(-1, 1))
                    finally:
                        del video
                if not raw.is_file():
                    raise RuntimeError("Wan did not save a video")
                source = media.inspect(raw)
                media.stream(source, "video")
                ratio = float(scene["duration"]) / source["duration"]
                core.run("ffmpeg", "-y", "-i", str(raw), "-vf", f"setpts={ratio:.9f}*(PTS-STARTPTS),fps=24",
                         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(scene["duration"]),
                         "-movflags", "+faststart", str(output))
                media.validate_clip(output, scene["duration"])
            except Exception:
                output.unlink(missing_ok=True)
                raise
            finally:
                raw.unlink(missing_ok=True)
