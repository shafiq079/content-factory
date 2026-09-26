"""Automated evidence review gate: repair, approval, blocking and migration."""
from pathlib import Path

from fastapi.testclient import TestClient

from app import contracts, core, providers, research
from app.main import app
from test_pipeline import wait_for_completion


def _source() -> research.Source:
    return research.Source(
        id=42,
        title="Moon science",
        url="https://science.gov/moon",
        domain="science.gov",
        publisher="Science Agency",
        excerpt="The Moon has impact craters across its surface.",
        evidence="The Moon has impact craters across its surface.",
    )


def _manifest() -> dict:
    source = _source()
    return {
        "schema_version": contracts.SCHEMA_VERSION,
        "id": "a" * 32,
        "request": core.Request(
            topic="Moon craters",
            duration=10,
            planner_provider="ollama",
            research_provider="wikipedia",
        ).model_dump(),
        "status": "running",
        "stage": "planning",
        "scenes": [
            {
                "id": 1,
                "duration": 5,
                "planned_duration": 5,
                "beat": "hook",
                "narration": "The Moon is made of cheese.",
                "visual_prompt": "The Moon in space",
                "source_ids": [42],
            },
            {
                "id": 2,
                "duration": 5,
                "planned_duration": 5,
                "beat": "ending",
                "narration": "Look closer at the surface.",
                "visual_prompt": "Close view of the lunar surface",
                "source_ids": [],
            },
        ],
        "assets": {},
        "error": None,
        "revision": 0,
        "research": [source.model_dump()],
        "research_brief": research.ResearchBrief(mode="wikipedia").model_dump(),
        "claim_review": contracts.ClaimReview().model_dump(),
        "caption_style": "classic",
        "music": None,
        "sfx": [],
    }


def test_automated_review_repairs_then_approves_and_caches(monkeypatch):
    manifest = _manifest()
    request = core.Request.model_validate(manifest["request"])
    calls = []

    def fake_review(prompt, model):
        calls.append((prompt, model))
        if len(calls) == 1:
            return {"scenes": [
                {
                    "scene_id": 1,
                    "factual": True,
                    "verdict": "revise",
                    "source_ids": [42],
                    "claims": ["The Moon has impact craters."],
                    "reason": "The cheese statement is absent from the evidence.",
                    "replacement_narration": "The Moon has impact craters.",
                },
                {
                    "scene_id": 2,
                    "factual": False,
                    "verdict": "approved",
                    "source_ids": [],
                    "claims": [],
                    "reason": "Connective narration.",
                    "replacement_narration": "",
                },
            ]}
        return {"scenes": [
            {
                "scene_id": 1,
                "factual": True,
                "verdict": "approved",
                "source_ids": [42],
                "claims": ["The Moon has impact craters."],
                "reason": "Directly supported by source 42.",
                "replacement_narration": "",
            },
            {
                "scene_id": 2,
                "factual": False,
                "verdict": "approved",
                "source_ids": [],
                "claims": [],
                "reason": "Connective narration.",
                "replacement_narration": "",
            },
        ]}

    monkeypatch.setattr(core, "_ollama_json", fake_review)
    core.ensure_claim_review(manifest, request)

    assert manifest["scenes"][0]["narration"] == "The Moon has impact craters."
    assert manifest["claim_review"]["status"] == "approved_after_revision"
    assert manifest["claim_review"]["attempts"] == 2
    assert manifest["claim_review"]["items"][0]["verdict"] == "approved"
    assert manifest["claim_review"]["fingerprint"] == core.claim_review_fingerprint(manifest)

    # Same factual inputs must reuse the saved approval rather than call Ollama again.
    core.ensure_claim_review(manifest, request)
    assert len(calls) == 2


def test_blocked_review_stops_before_video_generation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    source = _source()
    video_calls = []

    class FakeResearch:
        limitations = []
        def fetch(self, topic):
            return [source]

    class FakePlanner:
        def plan(self, request, sources):
            plan = core.TemplatePlanner().plan(request, sources)
            plan["scenes"][0]["narration"] = "The Moon contains hidden cities."
            plan["scenes"][0]["source_ids"] = [42]
            return plan

    class LoggedVideo:
        def generate(self, scene, output, request):
            video_calls.append(scene["id"])
            core.PreviewVideo().generate(scene, output, request)

    def blocked_review(prompt, model):
        return {"scenes": [
            {
                "scene_id": 1,
                "factual": True,
                "verdict": "blocked",
                "source_ids": [42],
                "claims": ["The Moon contains hidden cities."],
                "reason": "The cited evidence does not support this claim.",
                "replacement_narration": "",
            },
            {
                "scene_id": 2,
                "factual": False,
                "verdict": "approved",
                "source_ids": [],
                "claims": [],
                "reason": "Closing narration.",
                "replacement_narration": "",
            },
        ]}

    monkeypatch.setitem(providers.REGISTRY["research"], "broader", providers.Adapter(FakeResearch, providers.ready))
    monkeypatch.setitem(providers.REGISTRY["planner"], "ollama", providers.Adapter(FakePlanner, providers.ready))
    monkeypatch.setitem(providers.REGISTRY["video"], "preview", providers.Adapter(LoggedVideo, providers.ready))
    monkeypatch.setattr(core, "_ollama_json", blocked_review)

    with TestClient(app) as client:
        response = client.post("/projects", json={
            "topic": "Moon craters",
            "duration": 10,
            "width": 256,
            "height": 448,
            "planner_provider": "ollama",
            "research_provider": "broader",
        })
        assert response.status_code == 202, response.text
        project = wait_for_completion(client, response.json()["id"])
        assert project["status"] == "failed"
        assert "ClaimReviewBlocked" in project["error"]
        assert project["claim_review"]["status"] == "blocked"
        assert video_calls == []
        assert not (tmp_path / project["id"] / "final.mp4").exists()


def test_creative_or_preview_path_needs_no_review(monkeypatch):
    manifest = _manifest()
    manifest["research"] = []
    manifest["research_brief"] = research.ResearchBrief(mode="none").model_dump()
    manifest["request"]["planner_provider"] = "template"
    request = core.Request.model_validate(manifest["request"])
    monkeypatch.setattr(core, "_ollama_json", lambda *args: (_ for _ in ()).throw(AssertionError("reviewer called")))

    core.ensure_claim_review(manifest, request)

    assert manifest["claim_review"]["status"] == "not_required"
    assert manifest["claim_review"]["attempts"] == 0


def test_v6_migrates_to_unreviewed_claim_gate():
    old = {
        "schema_version": 6,
        "id": "b" * 32,
        "request": {},
        "status": "complete",
        "stage": "complete",
        "scenes": [],
        "assets": {},
        "error": None,
        "revision": 0,
        "research": [_source().model_dump()],
        "research_brief": research.ResearchBrief(mode="wikipedia").model_dump(),
    }
    upgraded = contracts.migrate_timeline(old)
    assert upgraded["schema_version"] == contracts.SCHEMA_VERSION
    assert upgraded["claim_review"]["status"] == "unreviewed"
    assert upgraded["claim_review"]["limitations"]
