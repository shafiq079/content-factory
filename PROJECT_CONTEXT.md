# Content Factory — Project Context

> **Read this file first when continuing development in another ChatGPT chat, Work session, Codex session, IDE, or coding agent.**
>
> This is the canonical handoff document for the project. Keep it updated whenever architecture, priorities, provider choices, data contracts, or major implementation decisions change.

## 1. Product Goal

Content Factory is a local/self-hosted AI content generation system for creating high-quality short-form videos from a topic or niche.

Phase 1 intentionally focuses on **content generation only**. Social publishing, scheduling, platform analytics, and n8n-style posting automation are postponed.

The target user flow is:

```
Topic / niche
  -> research / narrative angle
  -> full script
  -> intelligent scene plan
  -> AI-generated video clips
  -> narration and/or native scene audio
  -> captions
  -> FFmpeg editing / assembly
  -> final MP4 + editable project assets
```

Typical output is a 30–120 second vertical video, especially 60–120 second reels/shorts. A long video is not generated in one model call. The system plans multiple short scenes, generates them separately, and joins them into one final video.

## 2. Core Product Principles

- Prefer open-source/local/self-hosted models and avoid per-video paid generation APIs.
- GPU compute can be rented later; the software architecture should not depend on a specific GPU provider.
- Do not use stock footage as the primary visual generation path.
- Keep all major AI components behind adapters so providers can be replaced later.
- Preserve editable assets instead of producing only a flattened MP4.
- Scene-level regeneration should not require regenerating the whole project.
- Quality matters more than full automation during Phase 1.
- GPU-dependent validation is deferred until GPU hardware is available. Development should continue on CPU-testable architecture and contracts.

## 3. Current Stack

### Frontend
- Next.js
- Project creation form
- Project status polling
- Final video preview
- Scene prompt editing
- Per-scene audio mode controls
- LTX Fast / Quality selector
- Scene regeneration
- Caption-style rerendering
- Scene clip and narration audio replacement, scene text editor and grouped scene controls

### Backend
- FastAPI
- Local SQLite job queue
- Persistent worker
- Filesystem project storage
- Pydantic request/timeline contracts
- CPU test suite through GitHub Actions

### AI / Media
- Planner: Ollama-compatible local LLM
- Research v2: Wikipedia article extracts plus optional self-hosted SearXNG search and Trafilatura page extraction
- Video: LTX 2.5
- Narration: Kokoro with project-level voice ID / blend / speed settings and cached per-language runtime
- Captions: script timing or faster-whisper
- Editing / assembly: FFmpeg
- Editable interchange: OpenTimelineIO when installed

## 4. Video Generation Architecture

### LTX 2.5 Fast Mode

Uses the official `DistilledPipeline`.

Use cases:
- drafts
- iteration
- faster scene regeneration
- lower-cost GPU use later

The runtime is loaded once and reused across scenes instead of launching a new Python process per clip.

### LTX 2.5 Quality Mode

Uses the official `DFRPipeline` (Diffusion Fidelity Rendering).

DFR uses:
- the same distilled transformer
- the same text encoder
- video/audio VAEs
- spatial upscaler
- the extra detailing IC-LoRA

Current DFR settings:
- spatial refinement enabled
- one spatial upscaling round
- temporal upscaling disabled for now
- the temporal-upscaler checkpoint is therefore not required yet

Only one heavyweight LTX runtime is kept alive. Switching Fast <-> Quality replaces the active runtime rather than keeping both large pipelines resident in GPU memory.

## 5. Audio Architecture

Every scene supports one of three modes:

### `narration`
- AI-generated video
- dedicated Kokoro narration
- LTX native audio is not used in final assembly

Best for faceless/documentary/explainer content.

### `native`
- LTX video
- LTX synchronized audio/dialogue
- no separate Kokoro voice asset required

Best for scenes where a visible character/person should speak or where native synchronized sound is the point of the shot.

### `hybrid`
- LTX native ambience/SFX retained
- Kokoro narration mixed over it
- native track defaults to a lower volume

Best for cinematic narration where environmental sound improves the scene.

FFmpeg performs the scene audio routing and mix.

### Background music and SFX master layer

Implemented after scene assembly:
- project-scoped background music assets under `audio/music/`
- project-scoped sound effects under `audio/sfx/`
- common audio uploads are limited to 30 MB and validated as decodable audio-only media before use
- background music metadata: provider, asset, enabled, volume, loop, fade-in and fade-out
- SFX metadata: stable ID, provider, asset, enabled, timeline start, duration, volume and fades
- music and SFX are mixed over the already assembled narration/native/hybrid scene audio, so source scene media is never destructively rewritten
- short music can loop to the final timeline duration; non-looped music plays once and is trimmed when necessary
- music at volume 0 is skipped before its file is opened
- SFX are delayed to their timeline start and clipped to the remaining project duration
- final audio uses an FFmpeg limiter after mixing to reduce clipping risk
- music/SFX changes are render-only jobs and never call the video model
- timeline JSON is canonical; OTIO export also records music/SFX metadata
- current providers are project uploads, but the timeline/provider contract is intentionally ready for later local/generated music or SFX providers

The implementation uses MoneyPrinterTurbo's MIT-licensed BGM behavior as a design reference for practical concerns such as safe uploads, source validation, zero-volume short-circuiting, looping and fades. Content Factory keeps its own FFmpeg/timeline-oriented implementation rather than adopting MoneyPrinterTurbo's MoviePy pipeline.

### Reusable narration voice controls

Kokoro narration now has project-level settings:
- `voice_id`: a Kokoro voice ID or comma-separated blend
- `voice_speed`: 0.5–2.0, default 1.0
- blank voice IDs resolve to a language-appropriate default (or `KOKORO_VOICE`) and the resolved value is persisted for reproducibility
- voice IDs are restricted to safe model identifiers and must match the selected language prefix
- Kokoro `KPipeline` instances are cached per language and reused across scenes/jobs
- completed Kokoro projects can be **revoiced without calling the video model**: narration is regenerated, timings/captions are rebuilt, and the existing scene clips are looped/trimmed during FFmpeg rerender as needed
- narration-only preflight intentionally does not require the LTX/GPU stack

## 6. Editable Project Model

Each project stores:
- `timeline.json` as the canonical project definition
- scene clips
- narration WAV files when required
- captions SRT
- final MP4
- intermediate render files
- optional `.otio` export

The timeline stores scene order, prompts, narration, scene duration, start times, source IDs, audio mode, generation mode, transition mode/duration, status, active asset paths, original asset paths and asset origin. Schema v6 migrates v5 and older projects; uploaded/generated files use unique paths and stay on disk after later edits.

Scene editor action boundaries:
- Replace video: validate/transcode a project-scoped upload, switch the active clip, rebuild captions/render; no video model, no TTS. Native mode requires an audio stream.
- Edit narration text: narration/hybrid only, synthesize one scene's voice into a new file, measure its duration, recalculate subsequent starts and rebuild captions/render; no video model. Native dialogue is embedded in the clip and cannot be changed by text edit.
- Replace narration audio: audio-only upload transcoded to PCM WAV; switch active voice, use measured duration, recalculate and render; no TTS or video model.
- Change audio mode: render from current assets when compatible; a missing narration voice triggers only scene TTS. Native mode requires active clip audio.
- Change prompt and regenerate video: the video provider runs for that scene only, writing a versioned clip; old source assets remain available.
- Music/SFX start times are absolute project timeline positions and are preserved when narration lengths change. Users may need to reposition SFX after timing edits.
- The import layer accepts a limited set of video/audio extensions, enforces size and stream constraints, and transcodes into project-scoped media. Uploads are manual editor assets; LTX remains the primary generation provider.

## 7. Current Scene Generation Flow

For narration/hybrid scenes:
1. Generate narration first.
2. Measure actual narration duration.
3. Use that measured duration as the scene generation target.
4. Generate the scene clip.
5. Render narration or hybrid audio.

For native scenes:
1. Use the planned scene duration.
2. Generate LTX clip with synchronized native audio.
3. Validate that an audio stream exists.
4. Use that native audio in final assembly.

After all scenes:
- recalculate timeline start times
- generate captions
- normalize/render scenes
- concatenate
- burn captions
- validate final MP4
- export OTIO if available

## 8. AI Director / Scene Planner

### Previous behavior

The first implementation split the requested video into roughly equal ~7-second scenes. This was acceptable for a pipeline prototype but is not the desired final director behavior.

### AI Director v2

Implemented behavior:
- creates a narrative angle, story arc and project-level visual bible
- chooses a practical scene count based on total duration
- uses variable 3–8 second scenes instead of forcing equal ~7-second chunks
- normalizes scene durations to the exact requested total while preserving relative pacing
- assigns narrative beats: hook, setup, build, reveal, payoff, CTA or ending
- forces the first scene to be the hook
- requires the last scene to be an ending or natural CTA
- constrains narration length to a practical speaking rate
- stores camera direction, continuity guidance and source IDs
- chooses narration / native / hybrid audio modes when the selected video provider supports native audio
- appends continuity and the visual bible to actual visual prompts so future generated shots receive that context
- may select cut, black fade or white fade; the renderer supports these duration-preserving transitions

The deterministic template planner follows the same contract so all of this can be exercised in CPU-only CI.

### Timeline transitions and scene order

Implemented render-only editing:
- scenes can be moved earlier/later without regenerating any clip or narration
- a transition belongs to the **incoming** scene
- supported transition modes are `cut`, `fade` (through black), and `fade_white`
- the first scene is always forced to `cut`
- fade duration is 0.2–2.0 seconds; `cut` always stores duration 0
- half of a fade is rendered at the end of the outgoing scene and half at the start of the incoming scene
- the matching scene audio is faded at the same seam
- transitions do not overlap clips, so total project duration, caption offsets and music/SFX timeline positions remain stable
- scene reordering rebuilds starts, hook, script and captions, then rerenders saved assets only
- transition and order edits never invoke the video model or require a GPU
- transition metadata is exported in OTIO clip metadata

MoneyPrinterTurbo's MIT-licensed video effects were inspected as a practical reference. It applies MoviePy fade/slide/zoom effects to clips. Content Factory keeps its own FFmpeg duration-preserving seam implementation because our narration/caption/music/SFX timeline must remain stable and editable.

## 9. Research v2

Research modes:
- `auto`: broader research for Ollama; no external research for the deterministic preview planner.
- `broader`: optional local SearXNG JSON search plus Wikipedia references. If search is absent/unavailable, use Wikipedia and record the limitation. Without usable reference or web text, stop before factual planning.
- `wikipedia`: article introductions retrieved via Wikimedia's extracts API, not search-result snippets.
- `none`: creative/fiction use without external citations.

SearXNG is operator-configured with `SEARXNG_URL` pointing to an HTTP loopback origin (publish a container port to loopback). Enable the instance's JSON result format. No paid search API is required. The broader provider bounds results, ranks clear primary .gov/.edu/.int sources and topic-relevant titles, filters obvious navigation/spam, deduplicates URLs/text and selects diverse domains. These are transparent selection rules, not truth scores. Web HTML is fetched over HTTPS on port 443 with validated public DNS pinned to the TLS socket, revalidated redirects, response type/size limits and timeouts. Trafilatura >=2.0 (Apache 2.0) extracts main text and available page metadata; unsupported, private, binary, huge or inaccessible pages are skipped. Wikipedia uses its existing public API. Neither service is an unrestricted crawler.

Timeline schema v6 saves `research[]` Source records: stable numeric ID (Wikipedia page ID or deterministic URL hash within JavaScript's safe integer range), title, canonical URL, domain, publisher, available publication date, retrieval timestamp, excerpt, verbatim selected evidence and provider. `research_brief` stores the effective mode, limitations and possible numerical disagreements identified by a deliberately narrow cross-source sentence comparison. The selected sources are persisted before planning, so retries reuse them instead of repeating fetches. Existing v5 manifests get a legacy brief and retain their sources.

AI Director receives a structured evidence pack with IDs and possible conflicts and is instructed to cite factual scenes, avoid invented numbers/dates/names, and qualify weak/conflicting evidence; creative portions do not need citations. The plan validator rejects unknown IDs and precise figures missing from cited passages. It refuses an unqualified disputed figure when a possible numerical disagreement concerns a cited source. This only catches a narrow subset of factual errors: citation relevance, context, naming and actual truth still require review. The frontend displays evidence, source links/publisher/date, limitations, potential conflicts and citing scene IDs. External source text is treated as untrusted instructions in the Director prompt.

## 10. Current Repository State

Repository:
`shafiq079/content-factory`

Important completed milestones:
- end-to-end CPU preview pipeline
- persistent local job queue and retry/recovery
- editable timeline
- scene regeneration
- Kokoro narration adapter
- faster-whisper caption option
- persistent LTX 2.5 runtime
- scene-level narration/native/hybrid audio routing
- LTX Distilled Fast mode
- LTX DFR Quality mode
- frontend controls for video quality and audio routing
- reusable Kokoro voice ID/blend/speed controls and GPU-independent project revoice workflow
- editable project background music and timeline SFX with render-only FFmpeg mixing
- render-only scene ordering plus black/white fade transition controls
- scene clip/voice import, narration text and mode editing without unrelated model calls; immutable source assets and timeline v5 migration
- Research v2 structured source evidence, optional SearXNG, safer article extraction, conflict review, numeric grounding and timeline v6 migration
- GitHub Actions frontend build + backend CPU tests

### Most recent completed development work

**Research v2** adds a source-backed research pack to the director path. SearXNG search is optional and self-hosted; Wikipedia remains the reference fallback. Trafilatura extracts public pages with bounded secure retrieval. Projects retain source evidence, provenance and an explicit research brief in timeline v6. Scene IDs point to the saved sources; the planning contract rejects unsupported precise figures and flags possible numerical disagreements for review. These checks do not constitute automatic fact checking. GPU generation remains unverified. Next recommended task: build a review/approval step for factual scene claims when observed content quality warrants it; focused batch editing is the next smaller editor task.

## 11. Important Source Files

- `backend/app/core.py`
  - request model
  - planners
  - video/voice providers
  - pipeline orchestration
  - captions
  - rendering
  - LTX Fast/DFR runtime

- `backend/app/contracts.py`
  - scene/timeline schemas
  - plan validation
  - migrations

- `backend/app/providers.py`
  - provider registry
  - preflight checks

- `backend/app/research.py`
  - bounded research adapters, secure page retrieval, extracted evidence and conflict hints

- `backend/app/jobs.py`
  - persistent SQLite job queue
  - regeneration/rerender jobs

- `backend/app/media.py`
  - ffprobe validation
  - audio/video/caption checks

- `backend/app/audio.py`
  - project audio upload validation
  - background music/SFX asset handling
  - final FFmpeg master audio mix

- `backend/app/scene_assets.py`
  - safe project-scoped clip/voice import and transcode

- `frontend/app/SceneEditor.tsx`
  - grouped scene video, audio, transition and ordering actions

- `frontend/app/page.tsx`
  - current product UI

- `backend/tests/`
  - CPU-safe contracts and integration tests

- `backend/ltx-models.example.json`
  - expected LTX checkpoint configuration

## 12. GPU Status

There is currently **no GPU available for this project**.

Therefore:
- do not block development on real LTX generation
- do not repeatedly ask for GPU validation
- do not claim generated LTX footage has been tested
- use fake adapters / mocks / CPU previews for architecture tests
- save GPU validation for later

When a GPU becomes available, the first validation should compare identical prompts through Fast and Quality modes and record:
- load time
- per-scene generation time
- VRAM usage
- output resolution
- audio behavior
- visual quality
- consistency across multi-scene projects

## 13. Near-Term Roadmap

Current priority order:

1. Grounded-scene review and approval before rendering if content reviews reveal unsupported claims
2. Focused scene batch editing if real use calls for it
3. Add another video provider such as Wan
4. Real GPU validation of LTX Fast vs DFR Quality
5. Quality tuning based on real generated outputs
6. Social publishing/analytics only after generation quality is proven

## 14. Development Rules for Future Agents

When continuing this project:

- Read this file before changing architecture.
- Inspect current `main` and any active feature branch before coding.
- Preserve provider abstractions.
- Do not replace AI video generation with stock footage.
- Do not add social publishing during Phase 1.
- Do not add infrastructure only because it was previously mentioned; add it when actually needed.
- Keep CPU tests meaningful and avoid fake claims about GPU/model output.
- Prefer finishing core generation behavior over polishing minor UI details.
- Update this file when a major design choice, milestone, provider, contract, or roadmap item changes.
- Keep README focused on setup/user-facing behavior; keep deeper handoff context here.

## 15. Definition of the First Major Product Milestone

The first real milestone is:

```
one topic
 -> automatic narrative/script
 -> intelligent scene plan
 -> generated scene clips
 -> correct scene-level audio routing
 -> captions
 -> automatic edit/assembly
 -> playable final MP4
 -> editable project assets/timeline
```

No social upload is required for this milestone.
