"""The AI director consumes attributable notes and persists one coherent script."""
import io
import json

import pytest

from app import contracts, core, research


def test_research_sources_and_director_plan(monkeypatch):
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
        payload = {"idea": "Show a black hole through gravitational effects", "scenes": [
            {"narration": "What if light could not escape?", "visual_prompt": "Light bends around a dark center", "source_ids": [42]},
            {"narration": "A black hole has gravity strong enough to trap it.", "visual_prompt": "Glowing gas circles a dark center", "source_ids": [42]}]}
        return OllamaReply(json.dumps({"response": json.dumps(payload)}).encode())

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_ollama)
    request = core.Request(topic="Black holes", duration=10)
    plan = contracts.validate_plan(core.OllamaPlanner().plan(request, sources), sources, request.duration)
    assert plan["hook"] == plan["scenes"][0]["narration"]
    assert plan["script"] == " ".join(scene["narration"] for scene in plan["scenes"])
    assert plan["scenes"][1]["source_ids"] == [42]
    bad = {**plan, "scenes": [{**plan["scenes"][0], "source_ids": [999]}, plan["scenes"][1]]}
    with pytest.raises(ValueError, match="source not in the research brief"):
        contracts.validate_plan(bad, sources, request.duration)
