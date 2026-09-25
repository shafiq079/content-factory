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

With Ollama selected, the pipeline first retrieves up to three Wikipedia introduction excerpts, saves their page links in the timeline, then asks the local AI director for a narrative angle and timed scenes. The first scene narration is the hook; the full script is assembled from the exact scene narration, so captions and script stay in sync. The director attaches page IDs to scenes it drew from. The UI shows the research notes and script for review. Wikipedia excerpts are shortened and attributed by link to each article; their text is under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). This is source context, **not automatic fact checking**: check claims and source fit before publishing. Choose **No external research** for fiction or topics without encyclopedia coverage. Template preview has no factual research.

## Real model setup on your GPU machine

1. Install and configure the [official LTX-2 repository](https://github.com/Lightricks/LTX-2) and its 2.5 distilled pipeline. Download the model files listed in its current README. The weights are large; the official ComfyUI workflow recommends CUDA with **32 GB+ VRAM and 100 GB+ disk**; this is a planning estimate, not a proven minimum for our Python configuration. Lower memory can sometimes use quantization/offload; test your own GPU before budgeting.
2. Install `backend/requirements-ai.txt` in an environment compatible with LTX's Torch/CUDA stack, `espeak-ng` for Kokoro pronunciation fallback, and the official `ltx_pipelines` package. LTX's official repo uses `uv sync --extra natten`; run the backend in that environment or expose the installed module to its Python interpreter.
3. Copy `backend/ltx-models.example.json` to a private absolute path and fill in paths to all five LTX 2.5 split checkpoints. Set `LTX_CONFIG` to that JSON file's absolute path. The backend now loads the official Python `DistilledPipeline` directly and keeps that model runtime alive inside the worker, so all scenes in a project — and later jobs using the same checkpoint set — reuse the loaded model instead of starting a new Python process for every clip. Model/checkpoint versions must agree. `LTX_SEED` optionally sets the base seed (default `42`; each scene offsets it by scene ID). Start with smaller generations on GPU; 1080×1920 output is the render target and LTX inference at this size may exhaust VRAM. The app currently requests a nearby multiple-of-64 LTX resolution.
4. Install and start [Ollama](https://github.com/ollama/ollama) and pull a JSON-capable model. Set `OLLAMA_MODEL` and optionally `OLLAMA_URL` (local service only). Set `CAPTION_PROVIDER=whisper` to transcribe Kokoro output with faster-whisper; optionally set `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`. Kokoro's configured voice is controlled with `KOKORO_VOICE`, default `af_heart` in English. Other voices/languages should be checked against the official Kokoro model before use.
5. In the UI choose **Ollama**, **LTX 2.5**, **Kokoro**. Before a project is queued, the backend checks the selected providers, their local dependencies and checkpoint paths, and the Ollama model. Missing setup returns a descriptive HTTP 422 error; no fake video is silently substituted.

## Editable output and behavior

Each project lives in `content-factory/projects/<uuid>/`: `timeline.json` (the canonical timeline), `clips/`, `voice/`, `captions.srt`, `final.mp4`, and intermediate `work/` files. If `opentimelineio` is installed, `timeline.otio` is also written. Timeline JSON contains request settings, research excerpts and URLs, idea, hook, full script, scene prompts, narration, source IDs, duration, start times, asset paths, state and error. Schema version 2 is validated when loaded; original unversioned manifests are upgraded in place and unknown future versions are rejected. The OTIO file is an interchange export, not the source of truth. Editor compatibility depends on the editor and available adapters.

Click **Regenerate scene** after editing its prompt. That regenerates the clip while reusing its existing narration; editing narration via the API regenerates the voice and clip. The worker measures the voice first, requests video at the measured length, then recalculates subsequent start times/captions and rerenders the final video. The original scene target is saved as `planned_duration`; `duration` is the measured spoken length when using Kokoro. Choose Classic, Bold or Minimal captions and click **Render again with saved scenes** to change the final MP4 without running any text, video or voice model again. At present changing an entire project's voice requires editing project JSON and a future voice control. An error in one scene leaves intermediate files available for inspection.

The server stores projects on disk and uses a SQLite job queue in the project directory. A worker claims one job at a time and renews a lease. On restart an expired lease can be claimed again and completed scene assets are reused after ffprobe checks; invalid clips or voices are regenerated. New files are checked before publication, and the final MP4 must contain H.264 video at the requested dimensions and AAC audio with the expected duration. Captions must be ordered and stay within the timeline. Jobs can be cancelled between stages or retried from the last saved scene. A currently running model or FFmpeg process is allowed to finish its step before cancellation takes effect. Use a shared local filesystem for the SQLite database and assets; this implementation is **single-host** and has no authentication or multi-host resource scheduler. Bind to loopback; add access controls and stronger resource management before public deployment. Longer renders at 1080×1920 can be CPU intensive.

## Scope and present limitations

- The template planner is a deterministic test fixture. Wikipedia introduction excerpts offer a limited initial research source; they may be incomplete or unsuitable for a topic. Ollama uses these notes but does not independently verify factual claims or validate whether each narration sentence is fully supported.
- The first real video provider is LTX 2.5 distilled. Each raw LTX scene file keeps the model's synchronized native audio track, but the current final assembly deliberately uses dedicated narration instead. Scene-level `native` / `narration` / `hybrid` audio routing is the next audio milestone. Clips can still loop after normalization when actual model output is shorter than the measured narration.
- Without `CAPTION_PROVIDER=whisper`, caption timing is estimated from the script and distributed evenly across each scene. Whisper mode transcribes generated voice with word timestamps but does not guarantee perfect forced alignment; review captions before publishing.
- The UI edits visual prompts and offers three caption styling presets. Music, sound effects, transitions beyond cuts, asset replacement and Wan are future additions.
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

1. Validate the persistent in-process LTX runtime on a GPU host; record one-time model load cost, per-scene runtime, VRAM and output dimensions, then run a multi-scene real project.
2. Add scene-level `narration`, `native` and `hybrid` audio routing so LTX ambience/effects can be preserved when useful while Kokoro remains the consistent narrator.
3. Add LTX 2.5 DFR as the slower production-quality mode while keeping Distilled as the fast generation mode.
4. Add voice controls, music/effects adapters and then a Wan provider; expand research and public-deployment controls after the core generation path is proven.

## GitHub development

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests run CPU preview integration and fake-adapter failure tests plus a production frontend build in GitHub Actions. No GPU is needed for these checks. A GPU validation workflow can be added when a runner and checkpoints are available.
