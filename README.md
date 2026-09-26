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

With Ollama selected, **Automatic research** uses a self-hosted SearXNG instance when configured and keeps Wikipedia as a fallback. Configure `SEARXNG_URL=http://127.0.0.1:8080` (publish a container's port to loopback if needed) and enable JSON in SearXNG's `search.formats`. If SearXNG is unavailable, the project uses available Wikipedia article introductions and records the limitation. Select **Wikipedia references only** for that mode explicitly or **No external research** for fiction/creative work. Automatic preview mode stays research-free unless you explicitly choose a research mode. No paid search API is required.

The research stage keeps up to six diverse sources, extracts readable page text, records titles, URLs, publishers/domains, retrieval times and publication dates when found, and selects short evidence passages. Web pages must be public HTTPS HTML and are fetched with size, redirect and timeout limits. The editable timeline stores the research pack and possible numerical disagreements. The AI Director is told to cite source IDs for factual scenes, avoid unsupported names/dates/numbers and qualify unresolved disagreements. The planning contract rejects unknown citations and precise figures absent from cited evidence.

For factual Ollama projects, a second **automated evidence review gate** now runs before expensive media generation and again whenever factual narration changes. It reviews each scene against only the saved evidence pack. Unsupported lines are automatically rewritten when a short evidence-backed repair is possible, then reviewed again. If the reviewer cannot establish support after the repair limit, the project fails before video generation/final rendering instead of silently publishing the claim. Set `OLLAMA_REVIEW_MODEL` to use a separate installed local reviewer model; otherwise the planner model is reused. Review results, revisions, reasons and source IDs are persisted in the timeline and shown in the UI.

This is still **not human fact checking**. Automated approval means the narration appears aligned with the saved passages; it does not prove the source itself is true, complete, current or interpreted correctly. Wikipedia text is under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); review other source licenses before reuse. Scenes are normally 3–8 seconds. Each scene retains its narrative beat, continuity guidance, audio mode and source IDs; the project review shows which scenes cite each source.

## Real model setup on your GPU machine

1. Install and configure the [official LTX-2 repository](https://github.com/Lightricks/LTX-2). This app supports the 2.5 **DistilledPipeline** for Fast mode and the official **DFR (Diffusion Fidelity Rendering)** production path for Quality mode. DFR uses the same distilled transformer plus the detailing IC-LoRA and a spatial refinement pass. The weights are large; test your own GPU before budgeting because DFR is slower and needs more VRAM than Distilled.
2. Install `backend/requirements-ai.txt` in an environment compatible with LTX's Torch/CUDA stack, `espeak-ng` for Kokoro pronunciation fallback, and the official `ltx_pipelines` package. LTX's official repo uses `uv sync --extra natten`; run the backend in that environment or expose the installed module to its Python interpreter.
3. Copy `backend/ltx-models.example.json` to a private absolute path and fill in the five shared LTX 2.5 component paths. To use **Quality / DFR**, also download the official `ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors` from Lightricks' separate IC-LoRA repository and set `detailing_lora`. Set `LTX_CONFIG` to this JSON file. Fast mode only requires the five shared paths; Quality mode refuses to start if `detailing_lora` is missing. The worker keeps one heavyweight LTX runtime alive and reuses it across scenes. Switching between Fast and Quality replaces the active runtime rather than keeping both 22B pipelines loaded. `LTX_SEED` optionally sets the base seed (default `42`; each scene offsets it by scene ID). The app requests a nearby multiple-of-64 LTX resolution; final FFmpeg output is still rendered to the requested project dimensions.
4. Install and start [Ollama](https://github.com/ollama/ollama) and pull a JSON-capable model. Set `OLLAMA_MODEL` and optionally `OLLAMA_URL` (local service only). Factual projects automatically run a second evidence-review pass; set `OLLAMA_REVIEW_MODEL` to another installed model if you want planner/reviewer separation, otherwise the planner model is reused. Set `CAPTION_PROVIDER=whisper` to transcribe Kokoro output with faster-whisper; optionally set `WHISPER_MODEL=small`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8`. Kokoro voice and speed are project settings in the UI. A blank voice ID resolves once to the language default (or `KOKORO_VOICE` if configured) and that resolved ID is persisted in the project so later regeneration is reproducible. Kokoro also supports comma-separated voice blends. Speed is limited by this app to `0.5–2.0`, with `1.0` as normal.
5. In the UI choose **Ollama**, **LTX 2.5**, **Kokoro**, then choose **Fast · Distilled** for drafts or **Quality · DFR production path** for final-quality generation. Before a project is queued, the backend checks the selected mode's dependencies and checkpoint paths plus the Ollama model. Missing setup returns a descriptive HTTP 422 error; no fake video is silently substituted.

## Editable output and behavior

Each project lives in `content-factory/projects/<uuid>/`: `timeline.json` (the canonical timeline), `clips/`, `voice/`, optional project-scoped `audio/music/` and `audio/sfx/` assets, `captions.srt`, `final.mp4`, and intermediate `work/` files. If `opentimelineio` is installed, `timeline.otio` is also written. Timeline JSON contains request settings, research sources and brief, idea, story arc, visual bible, hook, full script, scene beats, continuity notes, prompts, narration, source IDs, duration, start times, asset paths, state and error. Schema version 7 is validated when loaded; older manifests are upgraded in place and unknown future versions are rejected. Timeline v7 also stores the automated claim-review status, reviewer/model, evidence-linked scene results, automatic revisions, attempts and limitations. The OTIO file is an interchange export, not the source of truth. Editor compatibility depends on the editor and available adapters.

Project-level **Narration voice** controls let you change the Kokoro voice ID or speech speed after a project is complete. Applying them regenerates only narration/captions and rerenders from the existing scene clips; it does **not** call the video model, so this workflow does not require the LTX GPU stack. Kokoro pipelines are cached per language and reused across scenes/jobs instead of reloading the TTS model for every scene.

Completed projects also support editable **background music and timeline SFX** without any GPU work. Uploads are stored inside the project, limited to common audio formats and 30 MB, and probed as decodable audio before being published. Background music has volume, loop, fade-in and fade-out controls. SFX are separate timeline events with a start time, duration, volume and fades. FFmpeg mixes these overlays only after the scene narration/native/hybrid audio has already been assembled, so the original scene clips and voice assets remain unchanged. A zero-volume music track is short-circuited before the file is loaded. Music/SFX edits enqueue a render-only job and are saved in `timeline.json`; OTIO export carries the same audio metadata.

The BGM workflow was informed by the MIT-licensed MoneyPrinterTurbo project's practical handling of safe uploads, zero-volume short-circuiting, looping and fades, but this implementation is adapted to Content Factory's FFmpeg + editable-timeline architecture rather than copying its MoviePy pipeline.

The scene cards now provide render-only timeline controls. You can move a scene earlier/later without regenerating its clip, and choose how a scene enters from the previous scene: **Cut**, **Fade through black**, or **Fade through white**. Fade duration is editable from 0.2–2.0 seconds. The renderer splits the transition across the outgoing and incoming scene while preserving the exact project duration and existing caption/music/SFX timing; audio is faded at the same seam. Timeline edits rebuild captions when order changes and rerender from saved media only — no video model or GPU is used. Transition metadata is also included in OTIO export.

This transition work was informed by MoneyPrinterTurbo's MIT-licensed fade/slide/zoom effect implementation, but Content Factory intentionally uses a duration-preserving FFmpeg seam model that fits its editable narration/caption/audio timeline instead of adopting the MoviePy effect chain.

Each scene card groups its video, narration/audio, transition and position controls. **Regenerate video scene** uses the video model for that scene after a prompt edit; **Replace clip** imports a local video and rerenders without AI generation. Videos are limited to 150 MB and MP4/MOV/WebM/MKV; they are decoded and re-encoded to safe project-scoped MP4 files. **Apply text** regenerates only that scene's voice with Kokoro (or the silent preview adapter), rebuilds timings/captions and rerenders. **Replace narration audio** imports a local audio-only file up to 30 MB as PCM WAV and rerenders without TTS or video generation. The saved original generated clip and original voice remain available and the active asset/origin is recorded in `timeline.json` and OTIO. The importer rejects a clip without an audio track when the scene uses native audio.

Each scene can use `narration` (dedicated Kokoro/silent voice track), `native` (clip synchronized audio only), or `hybrid` (dedicated narration mixed over the clip native track). **Apply audio mode** rerenders saved assets; if narration does not yet exist, it generates that scene's voice. Native dialogue remains embedded in the clip, so narration text cannot change it directly. Hybrid native audio defaults to 22% volume and can be changed with `HYBRID_NATIVE_VOLUME=0..1`. Changing narration length recalculates subsequent scene starts and captions. Music/SFX event starts remain absolute project timeline positions, so review their placement after changing scene length. The original target is saved as `planned_duration`. Choose Classic, Bold or Minimal captions and click **Render again with saved scenes** to change the final MP4 without running any model again. An error in one scene leaves intermediate files available for inspection.

For repeated edits, check several scene cards or use **Select all**, then choose **Set transition** or **Set audio mode** in **Batch scene edits**. The editor shows **Render only** or **TTS + render** when a selected scene has no narration track. Fades cannot include the first scene; Cut uses zero duration. Native requires audio in every selected clip. The backend validates the entire selection, queues one persistent job, synthesizes only missing voices into new versioned assets, rebuilds captions once and produces one final render. Failed batches leave the previous timeline and active assets in place; retry the job after fixing the cause. No video model is called. The API accepts `POST /projects/{id}/scenes/batch` with `scene_ids` and either `{operation:"set_transition",transition:"fade",transition_duration:0.8}` or `{operation:"set_audio_mode",audio_mode:"hybrid"}`.

The server stores projects on disk and uses a SQLite job queue in the project directory. A worker claims one job at a time and renews a lease. On restart an expired lease can be claimed again and completed scene assets are reused after ffprobe checks; invalid clips or voices are regenerated. New files are checked before publication, and the final MP4 must contain H.264 video at the requested dimensions and AAC audio with the expected duration. Captions must be ordered and stay within the timeline. Jobs can be cancelled between stages or retried from the last saved scene. A currently running model or FFmpeg process is allowed to finish its step before cancellation takes effect. Use a shared local filesystem for the SQLite database and assets; this implementation is **single-host** and has no authentication or multi-host resource scheduler. Bind to loopback; add access controls and stronger resource management before public deployment. Longer renders at 1080×1920 can be CPU intensive.

## Scope and present limitations

- The template planner is a deterministic test fixture. Self-hosted search quality depends on the instance's engines, and many pages cannot be extracted (paywalls, PDF, scripts or large pages). Without SearXNG, automatic Ollama projects fall back to Wikipedia. Numeric checks, source IDs and the automated reviewer improve evidence alignment but do not prove every statement true or resolve all contextual disagreements. The reviewer is another LLM pass, not a human fact checker.
- The first real video provider is LTX 2.5 with two generation modes: `fast` uses the official DistilledPipeline and `quality` uses the official DFR production path with the detailing IC-LoRA and one spatial refinement round. Raw LTX scene files keep synchronized native audio. Final assembly supports scene-level `narration`, `native` and `hybrid` routing; hybrid lowers native audio under the dedicated narrator. `native` requires the active clip to contain an audio stream. Temporal DFR upscaling is intentionally disabled for now, so the separate temporal-upscaler checkpoint is not required.
- Without `CAPTION_PROVIDER=whisper`, caption timing is estimated from the script and distributed evenly across each scene. Whisper mode transcribes generated voice with word timestamps but does not guarantee perfect forced alignment; review captions before publishing.
- The UI edits visual prompts and narration, imports scene media, offers three caption styling presets, project background music, timeline SFX, scene reordering, duration-preserving transitions and focused transition/audio-mode batch edits. Multi-track editing, more transition families and Wan are future additions.
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

1. Add another modular video provider such as Wan, preserving the current provider and scene contracts.
2. When a GPU becomes available, validate LTX Fast vs DFR Quality and tune generation based on real outputs.

## GitHub development

See [CONTRIBUTING.md](CONTRIBUTING.md). Pull requests run CPU preview integration and fake-adapter failure tests plus a production frontend build in GitHub Actions. No GPU is needed for these checks. A GPU validation workflow can be added when a runner and checkpoints are available.
