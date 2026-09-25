"""Small, attributable research brief for the AI director."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field


WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "ContentFactory/0.1 (https://github.com/shafiq079/content-factory)"


class Source(BaseModel):
    id: int = Field(ge=1)
    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    excerpt: str = Field(min_length=1)


def _query(params: dict[str, str]) -> dict:
    url = WIKIPEDIA_API + "?" + urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": "2", **params})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


class NoResearch:
    def fetch(self, topic: str) -> list[Source]:
        return []


class WikipediaResearch:
    """Searches encyclopedia pages; excerpts are source material, not verified conclusions."""
    def fetch(self, topic: str) -> list[Source]:
        matches = _query({"list": "search", "srsearch": topic, "srnamespace": "0", "srlimit": "3"})["query"]["search"]
        if not matches:
            raise RuntimeError(f"No Wikipedia research found for: {topic}")
        ids = [int(item["pageid"]) for item in matches]
        pages = _query({"prop": "extracts", "pageids": "|".join(map(str, ids)),
                        "exintro": "1", "explaintext": "1", "exchars": "1200"})["query"]["pages"]
        by_id = {int(page["pageid"]): page for page in pages}
        sources = []
        for page_id in ids:
            page = by_id.get(page_id, {})
            excerpt = page.get("extract", "").strip()
            if excerpt:
                sources.append(Source(id=page_id, title=page["title"],
                                      url=f"https://en.wikipedia.org/?curid={page_id}", excerpt=excerpt))
        if not sources:
            raise RuntimeError(f"No usable Wikipedia excerpts found for: {topic}")
        return sources
