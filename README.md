# Content Factory — Phase 1 prototype

For a full architecture handoff and current development context, read [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) first.

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

With Ollama selected, the pipeline first retrieves up to three Wikipedia introduction excerpts, saves their page links in the timeline, then asks AI Director v2 for a narrative angle, story arc, visual bible and intentionally paced scenes. Scenes are normally 3–8 seconds instead of being forced into equal chunks. The first scene is the hook; the last scene closes with an ending or a natural CTA. Each scene stores its narrative beat, continuity guidance, camera direction, audio mode and source IDs. Narration length is validated against scene duration so the script stays speakable. The full script is assembled from the exact scene narration, so captions and script remain in sync. Wikipedia excerpts are shortened and attributed by link to each article; their text is under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). This is source context, **not automatic fact checking**: check claims and source fit before publishing. Choose **No external research** for fiction or topics without encyclopedia coverage. Template preview has no factual research.

## Real model setup on your GPU machine

1. Install and configure the [official LTX-2 repository](https://github.com/Lightricks/LTX-2). This app supports the 2.5 **DistilledPipeline** for Fast mode and the official **DFR (Diffusion Fidelity Rendering)** production path for Quality mode. DFR uses the same distilled transformer plus the detailing IC-LoRA and a spatial refinement pass. The weights are large; test your own GPU before budgeting because DFR is slower and needs more VRAM than Distilled.
2. Install `backend/requirements-ai.txt` in an environment compatible with LTX's Torch/CUDA stack, `espeak-ng` for Kokoro pronunciation fallback, and the official `ltx_pipelines` package. LTX's official repo uses `uv sync --extra natten`; run the backend in that environment or expose the installed module to its Python interpreter.
3. Copy `backend/ltx-models.example.json` to a private absolute path and fill in the five shared LTX 2.5 component paths. To use **Quality / DFR**, also download the official `ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors` from Lightricks' separate IC-LoRA repository and set `detailing_lora`. Set `LTX_CONFIG` to this JSON file. Fast mode only requires the five shared paths; Quality mode refuses to start if `detailing_lora` is missing. The worker keeps one heavyweight LTX runtime alive and reuses it across scenes. Switching between Fast and Quality replaces the active runtime rather than keeping both 22B pipelines loaded. `LTX_SEED` optionally sets the base seed (default `42`; each scene offsets it by scene ID). The app requests a nearby multiple-of-64 LTX resolution; final FFmpeg output is still rendered to the requested project dimensions.
4. Install and start [Ollama](https://github.com/ollama/ollama) and pull a JSON-capable model. Set `OLLAMA_MODEL` and optionally `OLLAMA_URL` (local service only). Set `CAPTION_PROVIDER=whisper` to transcribe Kokoro output with faster-whisper; optionally set `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`. Kokoro voice and speed are project settings in the UI. A blank voice ID resolves once to the language default (or `KOKORO_VOICE` if configured) and that resolved ID is persisted in the project so later regeneration is reproducible. Kokoro also supports comma-separated voice blends. Speed is limited by this app to `0.5–2.0`, with `1.0` as normal.
5. In the UI choose **Ollama**, **LTX 2.5**, **Kokoro**, then choose **Fast · Distilled** for drafts or **Quality · DFR production path** for final-quality generation. Before a project is queued, the backend checks the selected mode's dependencies and checkpoint paths plus the Ollama model. Missing setup returns a descriptive HTTP 422 error; no fake video is silently substituted.

## Editable output and behavior

Each project lives in `content-factory/projects/<uuid>/`: `timeline.json` (the canonical timeline), `clips/`, `voice/`, `captions.srt`, `final.mp4`, and intermediate `work/` files. If `opentimelineio` is installed, `timeline.otio` is also written. Timeline JSON contains request settings, research excerpts and URLs, idea, story arc, visual bible, hook, full script, scene beats, continuity notes, prompts, narration, source IDs, duration, start times, asset paths, state and error. Schema version 2 is validated when loaded; original unversioned manifests are upgraded in place and unknown future versions are rejected. The OTIO file is an interchange export, not the source of truth. Editor compatibility depends on the editor and available adapters.

Project-level **Narration voice** controls let you change the Kokoro voice ID or speech speed after a project is complete. Applying them regenerates only narration/captions and rerenders from the existing scene clips; it does **not** call the video model, so this workflow does not require the LTX GPU stack. Kokoro pipelines are cached per language and reused across scenes/jobs instead of reloading the TTS model for every scene.

Click **Regenerate scene** after editing its prompt or audio mode. Each scene can use `narration` (dedicated Kokoro/silent voice track), `native` (LTX synchronized audio only), or `hybrid` (dedicated narration mixed over the LTX native track). Hybrid native audio defaults to 22% volume and can be changed with `HYBRID_NATIVE_VOLUME=0..1`. Narration/hybrid scenes measure the voice first and request video at that length; native scenes use the planned scene duration. The worker then recalculates subsequent start times/captions and rerenders the final video. The original target is saved as `planned_duration`. Choose Classic, Bold or Minimal captions and click **Render again with saved scenes** to change the final MP4 without running any model again. An error in one scene leaves intermediate files available for inspection.

The server stores projects on disk and uses a SQLite job queue in the project directory. A worker claims one job at a time and renews a lease. On restart an expired lease can be claimed again and completed scene assets are reused after ffprobe checks; invalid clips or voices are regenerated. New files are checked before publication, and the final MP4 must contain H.264 video at the requested dimensions and AAC audio with the expected duration. Captions must be ordered and stay within the timeline. Jobs can be cancelled between stages or retried from the last saved scene. A currently running model or FFmpeg process is allowed to finish its step before cancellation takes effect. Use a shared local filesystem for the SQLite database and assets; this implementation is **single-host** and has no authentication or multi-host resource scheduler. Bind to loopback; add access controls and stronger resource management before public deployment. Longer renders at 1080×1920 can be CPU intensive.

## Scope and present limitations

- The template planner is a deterministic test fixture. Wikipedia introduction excerpts offer a limited initial research source; they may be incomplete or unsuitable for a topic. Ollama uses these notes but does not independently verify factual claims or validate whether each narration sentence is fully supported.
- The first real video provider is LTX 2.5 with two generation modes: `fast` uses the official DistilledPipeline and `quality` uses the official DFR production path with the detailing IC-LoRA and one spatial refinement round. Raw LTX scene files keep synchronized native audio. Final assembly supports scene-level `narration`, `native` and `hybrid` routing; hybrid lowers native audio under the dedicated narrator. `native` requires the generated clip to contain an audio stream. Temporal DFR upscaling is intentionally disabled for now, so the separate temporal-upscaler checkpoint is not required.
- Without `CAPTION_PROVIDER=whisper`, caption timing is estimated from the script and distributed evenly across each scene. Whisper mode transcribes generated voice with word timestamps but does not guarantee perfect forced alignment; review captions before publishing.
- The UI edits visual prompts and offers three caption styling presets. Music, sound effects, transitions beyond cuts, asset replacement and Wan are future additions.
- Generated footage has **not** been verified in this workspace because it has no NVIDIA GPU, official checkpoint files or Ollama/Kokoro installations. Only the preview mode was run end to end.

## Technical choices and licenses (checked 25 September 2026)

| Component | Decision | Important condition |
| --- | --- | --- |
| LTX 2.5 | Official Python `DistilledPipeline` (Fast) and `DFRPipeline` (Quality), split weights; local GPU inference | DFR uses the same distilled transformer plus the separate detailing IC-LoRA. LTX 2.x community license, **not** Apache. Read the current terms before distribution. |
| Wan 2.2 TI2V-5B | Strong next video adapter candidate | Official repo: Apache 2.0; its 720p single-GPU offload example specifies at least 24 GB VRAM. Not integrated yet. |
| Kokoro 82M | Local narration adapter | Official inference repo describes Apache-licensed weights and code; language and voice availability vary. |
| faster-whisper | Optional local word timestamp transcription | MIT implementation; CPU int8 is supported. Transcription is separate from precise forced alignment. |
| FFmpeg | Concatenation, scaling, audio and burned captions | System package/license depends on build options; project requires `subtitles` filter. |
| OpenTimelineIO | Optional `.otio` export | Apache 2.0; interchange availability depends on editor adapters. |

Official references: [LTX-2 inference and model paths](https://github.com/Lightricks/LTX-2), [LTX 2.x license](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x), [LTX ComfyUI hardware guidance](https://github.com/Lightricks/ComfyUI-LTXVideo), [Wan2.2](https://github.com/Wan-Video/Wan2.2), [Kokoro](https://github.com/hexgrad/kokoro), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [FFmpeg documentation](https://ffmpeg.org/documentation.html), [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO).

## Next engineering milestones

GPU validation is intentionally deferred until suitable hardware is available. Current development should continue on CPU-testable product and pipeline work.

1. Add background music / SFX adapters and better editing/timeline controls.
2. Improve project editing and then expand research/factual grounding.
3. Add another modular video provider such as Wan.
4. When a GPU becomes available, validate LTX Fast vs DFR Quality and tune generation based on real outputs.

## GitHub development

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests run CPU preview integration and fake-adapter failure tests plus a production frontend build in GitHub Actions. No GPU is needed for these checks. A GPU validation workflow can be added when a runner and checkpoints are available.
