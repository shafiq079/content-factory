# Content Factory — Phase 1 prototype

Local web app for turning a topic into an editable short video. This repository contains application code; it does **not** contain model weights. The honest pipeline test uses animated test cards and silent audio. The actual AI path requires a local LLM, a GPU with the official LTX 2.5 inference package/checkpoints, and Kokoro TTS. No paid video API is used.

## Run the pipeline test

Requires Python 3.12+, Node.js 20+, npm and FFmpeg with `libx264` and `subtitles` support.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. Default settings run the **preview**: deterministic scene plan, simple non-AI colored test clips, silent WAVs, estimated script captions and FFmpeg render. It produces a playable MP4 and editable assets, but is not a content-quality demo. The backend API is at `http://127.0.0.1:8000/docs`.

## Real model setup on your GPU machine

1. Install and configure the [official LTX-2 repository](https://github.com/Lightricks/LTX-2) and its 2.5 distilled pipeline. Download the model files listed in its current README. The weights are large; the official ComfyUI workflow recommends CUDA with **32 GB+ VRAM and 100 GB+ disk**; this is a planning estimate, not a proven minimum for our Python configuration. Lower memory can sometimes use quantization/offload; test your own GPU before budgeting.
2. Install `backend/requirements-ai.txt` in an environment compatible with LTX's Torch/CUDA stack, `espeak-ng` for Kokoro pronunciation fallback, and the official `ltx_pipelines` package. LTX's official repo uses `uv sync --extra natten`; run the backend in that environment or expose the installed module to its Python interpreter.
3. Copy `backend/ltx-models.example.json` to a private absolute path and fill in paths to all five LTX 2.5 split checkpoints. Set `LTX_CONFIG` to that JSON file's absolute path. The model itself is invoked with `python -m ltx_pipelines.distilled` using the documented split checkpoint arguments. Model/checkpoint versions must agree. Start with smaller generations on GPU; 1080×1920 output is the render target and LTX inference at this size may exhaust VRAM. The app currently requests a nearby multiple-of-64 LTX resolution.
4. Install and start [Ollama](https://github.com/ollama/ollama) and pull a JSON-capable model. Set `OLLAMA_MODEL` and optionally `OLLAMA_URL` (local service only). Set `CAPTION_PROVIDER=whisper` to transcribe Kokoro output with faster-whisper; optionally set `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`. Kokoro's configured voice is controlled with `KOKORO_VOICE`, default `af_heart` in English. Other voices/languages should be checked against the official Kokoro model before use.
5. In the UI choose **Ollama**, **LTX 2.5**, **Kokoro**. If a provider or checkpoint is missing, the project records a visible error; no fake video is silently substituted.

## Editable output and behavior

Each project lives in `content-factory/projects/<uuid>/`: `timeline.json` (the canonical timeline), `clips/`, `voice/`, `captions.srt`, `final.mp4`, and intermediate `work/` files. If `opentimelineio` is installed, `timeline.otio` is also written. Timeline JSON contains request settings, scene prompts, narration, duration, start times, asset paths, state and error. The OTIO file is an interchange export, not the source of truth. Editor compatibility depends on the editor and available adapters.

Click **Regenerate scene** after editing its prompt. That regenerates one clip and its voice, recalculates subsequent start times/captions, and rerenders the final video. At present changing an entire project's voice or caption style requires editing project JSON and a future rerender endpoint. An error in one scene leaves intermediate files available for inspection.

The server stores projects on disk and uses in-process background threads to avoid blocking API requests. This is suitable for a **single local development process only**: running jobs are lost if the process restarts; there is no authentication, durable queue, resource scheduler or database. Bind to loopback; add those controls before any multi-user or public deployment. Longer renders at 1080×1920 can be CPU intensive.

## Scope and present limitations

- The template planner is a deterministic test fixture. Ollama creates structured scenes but does not retrieve current sources or verify claims; topics needing research need an explicit cited retrieval step.
- The first real video provider is LTX 2.5 distilled. Its own native audio is discarded during final assembly in favor of dedicated narration; native synchronized effects are a later routing choice. Long narration may make a short generated clip loop.
- Without `CAPTION_PROVIDER=whisper`, caption timing is estimated from the script and distributed evenly across each scene. Whisper mode transcribes generated voice with word timestamps but does not guarantee perfect forced alignment; review captions before publishing.
- The UI edits visual prompts only. Music, sound effects, transitions beyond cuts, caption styling presets, asset replacement and Wan are future additions.
- Generated footage has **not** been verified in this workspace because it has no NVIDIA GPU, official checkpoint files or Ollama/Kokoro installations. Only the preview mode was run end to end.

## Technical choices and licenses (checked 25 September 2026)

| Component | Decision | Important condition |
| --- | --- | --- |
| LTX 2.5 | Official Python `ltx_pipelines.distilled`, split weights; local GPU inference | LTX 2.x community license, **not** Apache. Entities with annual revenue at least $10m need a paid license for commercial use. Read its full current terms before distribution. |
| Wan 2.2 TI2V-5B | Strong next video adapter candidate | Official repo: Apache 2.0; its 720p single-GPU offload example specifies at least 24 GB VRAM. Not integrated yet. |
| Kokoro 82M | Local narration adapter | Official inference repo describes Apache-licensed weights and code; language and voice availability vary. |
| faster-whisper | Optional local word timestamp transcription | MIT implementation; CPU int8 is supported. Transcription is separate from precise forced alignment. |
| FFmpeg | Concatenation, scaling, audio and burned captions | System package/license depends on build options; project requires `subtitles` filter. |
| OpenTimelineIO | Optional `.otio` export | Apache 2.0; interchange availability depends on editor adapters. |

Official references: [LTX-2 inference and model paths](https://github.com/Lightricks/LTX-2), [LTX 2.x license](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x), [LTX ComfyUI hardware guidance](https://github.com/Lightricks/ComfyUI-LTXVideo), [Wan2.2](https://github.com/Wan-Video/Wan2.2), [Kokoro](https://github.com/hexgrad/kokoro), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FFmpeg documentation](https://ffmpeg.org/documentation.html), [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO).

## Next engineering milestones

1. Run one scene on a selected GPU host, record VRAM/runtime and output dimensions, then tune resolution/quantization and model path setup.
2. Run a multi-scene real audio/video project; measure narration durations before requesting each clip and review caption accuracy/visual matching.
3. Add a Wan provider and a scene-level choice based on hardware, look and license; add a source-backed research stage for factual videos.
4. Add durable queued jobs, resumable scenes, render-only endpoint, sound design, moderation/review and public deployment controls if needed.

## GitHub development

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests run a CPU preview integration test and a production frontend build in GitHub Actions. No GPU is needed for these checks. A GPU validation workflow can be added when a runner and checkpoints are available.
