"""AI Director v2: attributable research, variable pacing and continuity metadata."""
import io
import json

import pytest

from app import contracts, core, research


def test_research_sources_and_director_v2_plan(monkeypatch):
    def fake_query(params):
        if params.get("list") == "search":
            return {"query": {"search": [{"pageid": 42}]}}
        return {"query": {"pages": [{"pageid": 42, "title": "Black hole",
                                      "extract": "A black hole has gravity strong enough that light cannot escape."}]}}

    monkeypatch.setattr(research, "_query", fake_query)
    sources = research.WikipediaResearch().fetch("black holes")
    assert sources[0].url == "https://en.wikipedia.org/?curid=42"

    class OllamaReply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_ollama(request, timeout):
        prompt = json.loads(request.data)["prompt"]
        assert "[42] Black hole" in prompt
        assert "do not make scenes equal" in prompt
        assert "visual_bible" in prompt
        assert "between 3 and 8 seconds" in prompt
        payload = {
            "idea": "Show a black hole through gravitational effects",
            "story_arc": "Open with an impossible question, explain the force, then finish on the consequence.",
            "visual_bible": "Deep-space documentary, realistic lensing, stable dark center, blue-white accretion glow.",
            "scenes": [
                {
                    "duration": 4,
                    "beat": "hook",
                    "narration": "What if even light could not escape?",
                    "visual_prompt": "Starlight bends sharply around a dark center",
                    "camera": "slow push in",
                    "transition": "cut",
                    "continuity": "Establish the black hole and blue-white glow.",
                    "audio_mode": "narration",
                    "source_ids": [42],
                },
                {
                    "duration": 6,
                    "beat": "ending",
                    "narration": "Its gravity is strong enough to trap light.",
                    "visual_prompt": "Glowing gas circles the same dark center",
                    "camera": "slow orbit",
                    "transition": "cut",
                    "continuity": "Keep the same black hole, glow and camera direction.",
                    "audio_mode": "narration",
                    "source_ids": [42],
                },
            ],
        }
        return OllamaReply(json.dumps({"response": json.dumps(payload)}).encode())

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_ollama)
    request = core.Request(topic="Black holes", duration=10)
    raw = core.OllamaPlanner().plan(request, sources)
    plan = contracts.validate_plan(raw, sources, request.duration)

    assert plan["hook"] == plan["scenes"][0]["narration"]
    assert plan["script"] == " ".join(scene["narration"] for scene in plan["scenes"])
    assert plan["story_arc"].startswith("Open with")
    assert "blue-white" in plan["visual_bible"]
    assert [scene["duration"] for scene in plan["scenes"]] == [4.0, 6.0]
    assert [scene["beat"] for scene in plan["scenes"]] == ["hook", "ending"]
    assert plan["scenes"][1]["source_ids"] == [42]
    assert "Project visual bible" in plan["scenes"][1]["visual_prompt"]

    bad = {**plan, "scenes": [{**plan["scenes"][0], "source_ids": [999]}, plan["scenes"][1]]}
    with pytest.raises(ValueError, match="source not in the research brief"):
        contracts.validate_plan(bad, sources, request.duration)


@pytest.mark.parametrize("duration", [10, 30, 60, 120])
def test_template_director_v2_produces_valid_variable_plan(duration):
    request = core.Request(topic="Black holes", duration=duration)
    raw = core.TemplatePlanner().plan(request, [])
    plan = contracts.validate_plan(raw, [], duration)

    assert sum(scene["duration"] for scene in plan["scenes"]) == pytest.approx(duration, abs=0.01)
    assert plan["scenes"][0]["beat"] == "hook"
    assert plan["scenes"][-1]["beat"] == "ending"
    assert all(3 <= scene["duration"] <= 8 for scene in plan["scenes"])
    assert all(scene["continuity"] for scene in plan["scenes"])
    assert plan["story_arc"]
    assert plan["visual_bible"]
    if len(plan["scenes"]) > 2:
        assert len({scene["duration"] for scene in plan["scenes"]}) > 1


def test_duration_normalization_preserves_pacing_and_total():
    result = core.normalize_scene_durations([4, 7, 5, 6], 24)
    assert sum(result) == pytest.approx(24, abs=0.01)
    assert all(3 <= value <= 8 for value in result)
    assert result[1] > result[0]


def test_director_contract_rejects_bad_story_structure_and_overlong_narration():
    base = {
        "idea": "A structured short",
        "story_arc": "Hook, explain, close.",
        "visual_bible": "One coherent visual world.",
        "scenes": [
            {
                "id": 1,
                "duration": 5,
                "beat": "hook",
                "narration": "A short opening hook.",
                "visual_prompt": "Opening shot",
            },
            {
                "id": 2,
                "duration": 5,
                "beat": "ending",
                "narration": "A clear final thought.",
                "visual_prompt": "Closing shot",
            },
        ],
    }
    valid = contracts.validate_plan(base, [], 10)
    assert valid["scenes"][-1]["beat"] == "ending"

    wrong_beat = {**base, "scenes": [{**base["scenes"][0], "beat": "setup"}, base["scenes"][1]]}
    with pytest.raises(ValueError, match="First scene must use the hook beat"):
        contracts.validate_plan(wrong_beat, [], 10)

    long_scene = {
        **base,
        "scenes": [
            {**base["scenes"][0], "narration": " ".join(["word"] * 20)},
            base["scenes"][1],
        ],
    }
    with pytest.raises(ValueError, match="narration is too long"):
        contracts.validate_plan(long_scene, [], 10)
