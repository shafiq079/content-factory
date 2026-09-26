"""Bounded, attributable research: Wikipedia and optional self-hosted search."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from pydantic import BaseModel, Field

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "ContentFactory/0.2 (https://github.com/shafiq079/content-factory)"
MAX_HTML = 700_000
NUMBERS = re.compile(r"(?<![\w.])\d[\d,.]*(?:%|\b)")
STOPWORDS = {"about", "after", "also", "been", "from", "have", "into", "more", "that", "their", "there", "these", "this", "those", "were", "which", "with", "year"}


class Source(BaseModel):
    id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(pattern=r"^https://", max_length=2000)
    excerpt: str = Field(min_length=1, max_length=3000)
    evidence: str = Field(default="", max_length=1000)
    domain: str = ""
    publisher: str = ""
    published_at: str | None = None
    retrieved_at: str | None = None
    provider: str = "wikipedia"


class Conflict(BaseModel):
    source_ids: list[int] = Field(min_length=2)
    summary: str = Field(max_length=400)


class ResearchBrief(BaseModel):
    mode: str = "none"
    limitations: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _query(params: dict[str, str]) -> dict:
    url = WIKIPEDIA_API + "?" + urllib.parse.urlencode({"action": "query", "format": "json", "formatversion": "2", **params})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}), timeout=15) as response:
        return json.load(response)


def _terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{4,}", text.lower()) if word not in STOPWORDS}


def _evidence(text: str, topic: str) -> str:
    """Choose relevant original sentences; no generated summaries."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 25]
    ranked = sorted(enumerate(sentences[:80]), key=lambda item: (-len(_terms(item[1]) & _terms(topic)), item[0]))
    return " ".join(sentences[i] for i in sorted(i for i, _ in ranked[:3]))[:900] or text[:900]


def _canonical(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        host, port = (parsed.hostname or "").lower().rstrip("."), parsed.port
    except ValueError as exc:
        raise ValueError("Invalid source URL") from exc
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        raise ValueError("Sources must use HTTPS on port 443")
    query = urllib.parse.urlencode(sorted((k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                                         if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}))
    return urllib.parse.urlunsplit(("https", host, parsed.path or "/", query, ""))


def _public_address(host: str) -> str:
    """Fail closed for mixed public/private DNS answers as well as literal IPs."""
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("Private or unsafe source address")
        return sorted(addresses)[0]
    except (OSError, ValueError) as exc:
        raise ValueError(f"Unsafe or unresolved source host: {host}") from exc


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, timeout=8, context=ssl.create_default_context())
        self.address = address

    def connect(self) -> None:
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _download_html(url: str) -> tuple[str, bytes]:
    """HTTPS-only with validated DNS pinned to TLS socket, including redirects."""
    for _ in range(3):
        url = _canonical(url)
        parsed = urllib.parse.urlsplit(url)
        connection = _PinnedHTTPS(parsed.hostname or "", _public_address(parsed.hostname or ""))
        try:
            connection.request("GET", urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, "")),
                               headers={"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect has no destination")
                url = urllib.parse.urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Page returned HTTP {response.status}")
            if "text/html" not in (response.getheader("Content-Type") or "").lower():
                raise ValueError("Source is not HTML")
            length = response.getheader("Content-Length")
            if length and int(length) > MAX_HTML:
                raise ValueError("Source page is too large")
            data = response.read(MAX_HTML + 1)
            if len(data) > MAX_HTML:
                raise ValueError("Source page is too large")
            return url, data
        finally:
            connection.close()
    raise ValueError("Too many page redirects")


def _searxng_url() -> str:
    value = os.getenv("SEARXNG_URL", "").strip()
    if not value:
        raise ValueError("SEARXNG_URL is not configured")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("SEARXNG_URL must be a loopback HTTP origin")
    return value.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _search(topic: str) -> list[dict]:
    url = _searxng_url() + "/search?" + urllib.parse.urlencode({"q": topic, "format": "json", "categories": "general"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as response:
        if "application/json" not in (response.headers.get("Content-Type") or "").lower():
            raise ValueError("SearXNG did not return JSON; enable format=json")
        payload = response.read(200_001)
        if len(payload) > 200_000:
            raise ValueError("Search response is too large")
        return json.loads(payload)["results"][:16]


def _extract(url: str, html: bytes, topic: str, title: str) -> Source:
    import trafilatura

    doc = trafilatura.bare_extraction(html, url=url, include_comments=False, include_tables=False)
    if not doc or not doc.text or len(doc.text.strip()) < 80:
        raise ValueError("Page had insufficient main text")
    text = re.sub(r"\s+", " ", doc.text).strip()[:3000]
    domain = urllib.parse.urlsplit(url).hostname or ""
    date = doc.date if isinstance(doc.date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.date) else None
    return Source(id=2**48 + int.from_bytes(hashlib.sha256(url.encode()).digest()[:6], "big"),
                  title=(doc.title or title)[:300], url=url, excerpt=text, evidence=_evidence(text, topic),
                  domain=domain, publisher=(doc.sitename or domain)[:120], published_at=date,
                  retrieved_at=_now(), provider="searxng")


def _rank(item: dict, topic: str) -> tuple[int, int]:
    domain = urllib.parse.urlsplit(item["url"]).hostname or ""
    primary = domain.endswith((".gov", ".edu", ".int")) or domain in {"who.int", "nasa.gov", "cdc.gov", "un.org"}
    return int(primary), len(_terms(topic) & _terms(item.get("title", "")))


class NoResearch:
    limitations: list[str] = []

    def fetch(self, topic: str) -> list[Source]:
        return []


class WikipediaResearch:
    """Actual Wikipedia introduction text from its extracts API."""
    limitations = ["Wikipedia is a reference source; it does not independently verify every claim."]

    def fetch(self, topic: str) -> list[Source]:
        matches = _query({"list": "search", "srsearch": topic, "srnamespace": "0", "srlimit": "4"})["query"]["search"]
        if not matches:
            raise RuntimeError(f"No Wikipedia research found for: {topic}")
        ids = [int(item["pageid"]) for item in matches]
        pages = _query({"prop": "extracts", "pageids": "|".join(map(str, ids)), "exintro": "1",
                        "explaintext": "1", "exchars": "2800"})["query"]["pages"]
        by_id = {int(page["pageid"]): page for page in pages}
        sources = []
        for page_id in ids:
            page = by_id.get(page_id, {})
            excerpt = re.sub(r"\s+", " ", page.get("extract", "")).strip()[:2800]
            if excerpt:
                sources.append(Source(id=page_id, title=page["title"],
                                      url=f"https://en.wikipedia.org/?curid={page_id}", excerpt=excerpt,
                                      evidence=_evidence(excerpt, topic), domain="en.wikipedia.org", publisher="Wikipedia",
                                      retrieved_at=_now(), provider="wikipedia"))
        if not sources:
            raise RuntimeError(f"No usable Wikipedia excerpts found for: {topic}")
        return sources


class BroaderResearch:
    """One bounded local search with extracted web pages and reference fallback."""
    def __init__(self):
        self.limitations: list[str] = []

    def fetch(self, topic: str) -> list[Source]:
        try:
            reference = WikipediaResearch().fetch(topic)
        except (RuntimeError, KeyError, OSError, ValueError):
            reference = []
            self.limitations.append("Wikipedia retrieval failed; review web sources carefully.")
        sources = reference[:2]
        try:
            results = _search(topic)
        except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
            self.limitations.append(f"Self-hosted search unavailable ({type(exc).__name__}); used Wikipedia references.")
            if reference:
                return reference[:3]
            raise RuntimeError("Neither web search nor Wikipedia produced usable research") from exc

        candidates = []
        seen_urls = {source.url for source in sources}
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            try:
                url = _canonical(item["url"])
            except ValueError:
                continue
            title = str(item.get("title") or url)[:300]
            path = urllib.parse.urlsplit(url).path.lower()
            if (url in seen_urls or re.search(r"/(search|tag|category|author|login|shopping|cart)(/|$)", path)
                    or re.search(r"\b(buy now|discount code|top 10 best)\b", title.lower())):
                continue
            seen_urls.add(url)
            candidates.append({"url": url, "title": title})

        seen_domains = set()
        fingerprints = {hashlib.sha256(source.excerpt[:800].lower().encode()).digest() for source in sources}
        for item in sorted(candidates, key=lambda entry: _rank(entry, topic), reverse=True)[:10]:
            domain = urllib.parse.urlsplit(item["url"]).hostname or ""
            if domain in seen_domains or domain == "en.wikipedia.org":
                continue
            try:
                final_url, html = _download_html(item["url"])
                if final_url in {source.url for source in sources}:
                    continue
                source = _extract(final_url, html, topic, item["title"])
            except (OSError, ValueError, TimeoutError, ssl.SSLError):
                continue
            if source.domain in seen_domains or (_terms(topic) and not (_terms(topic) & _terms(source.title + " " + source.evidence))):
                continue
            fingerprint = hashlib.sha256(source.excerpt[:800].lower().encode()).digest()
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            seen_domains.add(source.domain)
            sources.append(source)
            if len(sources) >= 6:
                break
        if not sources:
            raise RuntimeError("Research found no extractable sources; choose Wikipedia or no research")
        if len(sources) < 2:
            self.limitations.append("Only one usable source was retrieved; review its claims manually.")
        elif not any(source.provider == "searxng" for source in sources):
            self.limitations.append("Web results yielded no usable pages; this pack contains Wikipedia references only.")
        return sources


def detect_conflicts(sources: list[Source]) -> list[Conflict]:
    """Flag similar sentences with different figures; this is a review hint."""
    found = []
    for i, left in enumerate(sources):
        for right in sources[i + 1:]:
            if left.domain == right.domain:
                continue
            a_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", left.evidence or left.excerpt) if NUMBERS.search(s) and len(s) >= 30]
            b_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", right.evidence or right.excerpt) if NUMBERS.search(s) and len(s) >= 30]
            for a in a_sentences[:8]:
                for b in b_sentences[:8]:
                    common, union = _terms(a) & _terms(b), _terms(a) | _terms(b)
                    if len(common) >= 3 and len(common) / max(1, len(union)) >= 0.5 and set(NUMBERS.findall(a)) != set(NUMBERS.findall(b)):
                        found.append(Conflict(source_ids=[left.id, right.id],
                                              summary=f"Possible numerical disagreement: [{left.id}] {a[:140]} / [{right.id}] {b[:140]}"))
                        break
                else:
                    continue
                break
            if len(found) >= 5:
                return found
    return found


def brief(sources: list[Source], mode: str, limitations: list[str] | None = None) -> ResearchBrief:
    return ResearchBrief(mode=mode, limitations=limitations or [], conflicts=detect_conflicts(sources))


def director_evidence(sources: list[Source]) -> str:
    return json.dumps({"sources": [{"id": s.id, "title": s.title, "url": s.url,
                                    "publisher": s.publisher or s.domain, "published_at": s.published_at,
                                    "evidence": s.evidence or s.excerpt[:900]} for s in sources],
                       "possible_conflicts": [item.model_dump() for item in detect_conflicts(sources)]}, ensure_ascii=False)
