"""CPU-only research, safety and grounding checks; no external network."""
import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import contracts, core, providers, research
from app.main import app
from test_pipeline import wait_for_completion


def _source(id: int, domain: str, evidence: str) -> research.Source:
    return research.Source(id=id, title=f"Study {id}", url=f"https://{domain}/article",
                           domain=domain, publisher=domain, excerpt=evidence, evidence=evidence)


def test_broader_research_extracts_and_deduplicates(monkeypatch):
    wiki = _source(42, "en.wikipedia.org", "The moon has a surface with craters and long geological history.")
    monkeypatch.setattr(research.WikipediaResearch, "fetch", lambda self, topic: [wiki])
    monkeypatch.setattr(research, "_search", lambda topic: [
        {"url": "https://news.example/article?utm_source=search", "title": "Moon craters review"},
        {"url": "https://science.gov/moon", "title": "Moon crater science"},
        {"url": "https://science.gov/moon#section", "title": "Same page"},
        {"url": "https://news.example/article", "title": "Duplicate result"},
        {"url": "https://unrelated.example/tag/moon", "title": "Archive"},
        {"url": "http://127.0.0.1/metadata", "title": "Unsafe result"},
        {"url": "https://duplicate.example/moon", "title": "Same content"},
    ])
    visited = []
    def fake_download(url):
        visited.append(url)
        content = ("<html><head><title>Moon crater science</title></head><body><main><article>"
                   "<p>Scientists describe how impact craters form on the lunar surface when objects strike it.</p>"
                   "<p>These observations help researchers study the surface and the history of the Moon over time.</p>"
                   "<p>The data are collected by scientific instruments and compared with older observations.</p>"
                   "</article></main></body></html>").encode()
        if "news.example" in url:
            content = content.replace(b"impact craters form", b"surface craters form")
        return url, content
    monkeypatch.setattr(research, "_download_html", fake_download)
    pack = research.BroaderResearch().fetch("Moon craters")
    assert pack[0].id == 42
    assert pack[1].domain == "science.gov"  # primary sources rank ahead of news
    assert pack[1].provider == "searxng"
    assert "Scientists describe" in pack[1].excerpt
    assert "Moon" in pack[1].evidence
    assert len({s.url for s in pack}) == len(pack)
    assert len([url for url in visited if "science.gov" in url]) == 1
    assert not any("127.0.0.1" in url for url in visited)


def test_html_extraction_rejects_navigation_only():
    html = b"<html><head><title>Click</title></head><body><nav>Home Products Login</nav></body></html>"
    with pytest.raises(ValueError, match="insufficient main text"):
        research._extract("https://example.org/page", html, "topic", "Example")


def test_source_urls_block_local_dns_private_redirect_and_binary(monkeypatch):
    for url in ("http://example.com/article", "https://user:pass@example.org/", "https://example.org:8080/",
                "https://127.0.0.1/admin"):
        with pytest.raises(ValueError):
            research._download_html(url)

    def mixed_answers(host, port, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443))]
    monkeypatch.setattr(research.socket, "getaddrinfo", mixed_answers)
    with pytest.raises(ValueError, match="Unsafe"):
        research._download_html("https://safe.example/article")

    monkeypatch.setattr(research, "_public_address", lambda host: "8.8.8.8" if host == "safe.example" else (_ for _ in ()).throw(ValueError("Unsafe redirect")))
    class Redirect:
        status = 302
        def getheader(self, name):
            return "http://localhost/admin" if name == "Location" else None
    class Pinned:
        def __init__(self, *args): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Redirect()
        def close(self): pass
    monkeypatch.setattr(research, "_PinnedHTTPS", Pinned)
    with pytest.raises(ValueError, match="HTTPS"):
        research._download_html("https://safe.example/redirect")


def test_conflicts_and_grounding_contract():
    left = _source(101, "official.gov", "The city recorded 1,000 riders during the summer survey.")
    right = _source(102, "study.edu", "The city recorded 1,200 riders during the summer survey.")
    conflicts = research.detect_conflicts([left, right])
    assert len(conflicts) == 1
    plan = {"idea": "Rider count", "scenes": [
        {"id": 1, "duration": 5, "beat": "hook", "narration": "Why did ridership change?",
         "visual_prompt": "Cyclists in a city", "source_ids": []},
        {"id": 2, "duration": 5, "beat": "ending", "narration": "There were 1,000 riders.",
         "visual_prompt": "A city street", "source_ids": [101]}]}
    with pytest.raises(ValueError, match="qualification"):
        contracts.validate_plan(plan, [left, right], 10, conflicts)
    plan["scenes"][1]["narration"] = "One survey reported 1,000 riders."
    assert contracts.validate_plan(plan, [left, right], 10, conflicts)["scenes"][1]["source_ids"] == [101]
    plan["scenes"][1]["narration"] = "One survey reported 9,000 riders."
    with pytest.raises(ValueError, match="absent from its cited evidence"):
        contracts.validate_plan(plan, [left, right], 10, conflicts)
    plan["scenes"][1]["source_ids"] = []
    with pytest.raises(ValueError, match="without a source ID"):
        contracts.validate_plan(plan, [left, right], 10, conflicts)
    assert research.NUMBERS.findall("5% of 1,000") == ["5%", "1,000"]


def test_search_failure_preserves_reference_fallback(monkeypatch):
    monkeypatch.setattr(research.WikipediaResearch, "fetch", lambda self, topic: [_source(11, "en.wikipedia.org", "A reference article.")])
    monkeypatch.setattr(research, "_search", lambda topic: (_ for _ in ()).throw(OSError("offline")))
    provider = research.BroaderResearch()
    assert [s.id for s in provider.fetch("topic")] == [11]
    assert any("unavailable" in note for note in provider.limitations)


def test_research_pack_persists_and_reaches_planner(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    source = _source(42, "science.gov", "The Moon has impact craters on its surface.")
    class FakeResearch:
        limitations = []
        def fetch(self, topic):
            return [source]
    class FakePlanner:
        def plan(self, request, sources):
            assert [item.id for item in sources] == [42]
            assert '"id": 42' in research.director_evidence(sources)
            plan = core.TemplatePlanner().plan(request, sources)
            plan["scenes"][0]["source_ids"] = [42]
            return plan
    monkeypatch.setitem(providers.REGISTRY["research"], "broader", providers.Adapter(FakeResearch, providers.ready))
    monkeypatch.setitem(providers.REGISTRY["planner"], "template", providers.Adapter(FakePlanner, providers.ready))
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "Moon craters", "duration": 10, "width": 256,
                                                   "height": 448, "research_provider": "broader"})
        assert response.status_code == 202
        finished = wait_for_completion(client, response.json()["id"])
        assert finished["status"] == "complete", finished["error"]
        assert finished["research"][0]["id"] == 42
        assert finished["scenes"][0]["source_ids"] == [42]
        assert finished["research_brief"]["mode"] == "broader"
        assert json.loads((tmp_path / finished["id"] / "timeline.json").read_text())["research"][0]["url"] == source.url


def test_v5_migrates_research_brief():
    old = {"schema_version": 5, "id": "a" * 32, "request": {}, "status": "complete", "stage": "complete",
           "scenes": [], "assets": {}, "error": None, "revision": 0, "research": [
               _source(4, "en.wikipedia.org", "A reference article.").model_dump()]}
    upgraded = contracts.migrate_timeline(old)
    assert upgraded["schema_version"] == contracts.SCHEMA_VERSION
    assert upgraded["research"][0]["id"] == 4
    assert upgraded["research_brief"]["mode"] == "legacy"
    assert upgraded["claim_review"]["status"] == "unreviewed"


def test_creative_project_skips_network_research(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(research, "_query", lambda params: (_ for _ in ()).throw(AssertionError("Wikipedia contacted")))
    monkeypatch.setattr(research, "_search", lambda topic: (_ for _ in ()).throw(AssertionError("Search contacted")))
    with TestClient(app) as client:
        response = client.post("/projects", json={"topic": "A fictional world", "duration": 10,
                                                   "width": 256, "height": 448, "research_provider": "none"})
        assert response.status_code == 202
        project = wait_for_completion(client, response.json()["id"])
        assert project["status"] == "complete", project["error"]
        assert project["research"] == []
        assert project["research_brief"]["mode"] == "none"
