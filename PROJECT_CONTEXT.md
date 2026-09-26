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

### Backend
- FastAPI
- Local SQLite job queue
- Persistent worker
- Filesystem project storage
- Pydantic request/timeline contracts
- CPU test suite through GitHub Actions

### AI / Media
- Planner: Ollama-compatible local LLM
- Initial research: Wikipedia excerpts
- Video: LTX 2.5
- Narration: Kokoro
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

FFmpeg performs the final audio routing and mix.

## 6. Editable Project Model

Each project stores:
- `timeline.json` as the canonical project definition
- scene clips
- narration WAV files when required
- captions SRT
- final MP4
- intermediate render files
- optional `.otio` export

The timeline stores prompts, narration, scene duration, start times, source IDs, audio mode, generation mode, status, and asset paths.

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
- keeps transitions at `cut` for now because the renderer does not yet implement richer transitions

The deterministic template planner follows the same contract so all of this can be exercised in CPU-only CI.

## 9. Research Behavior

Current research is deliberately small:
- Wikipedia search
- up to a few introduction excerpts
- source URL saved in timeline
- source IDs attached to scenes

This is context for the planner, **not full fact checking**. Do not claim that current Wikipedia support independently verifies every narration sentence.

Broader research is a later task after the core generation pipeline is mature.

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
- GitHub Actions frontend build + backend CPU tests

### Most recent development work

**AI Director v2** is the current milestone being completed. Its implementation includes narrative beats, story arc, visual bible, variable scene timing, speakable narration checks and continuity metadata. Once its CI is green and it is merged, the next development priority is reusable voice controls.

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

- `backend/app/jobs.py`
  - persistent SQLite job queue
  - regeneration/rerender jobs

- `backend/app/media.py`
  - ffprobe validation
  - audio/video/caption checks

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

1. Finish CI/merge for **AI Director v2**
2. Voice controls and reusable voice configuration
3. Background music / SFX architecture
4. Better editing/transitions and timeline controls
5. Better project editing workflow
6. Broader research / stronger factual grounding
7. Add another video provider such as Wan
8. Real GPU validation of LTX Fast vs DFR Quality
9. Quality tuning based on real generated outputs
10. Social publishing/analytics only after generation quality is proven

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
