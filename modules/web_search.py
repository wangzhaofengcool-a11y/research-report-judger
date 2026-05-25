"""
WebSearch - perform web searches for fact-checking.
Supports Google, Bing, and DuckDuckGo (free, no API key).
Uses requests + threading for parallel searches.
"""

import concurrent.futures
from dataclasses import dataclass
import requests


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    credibility: str


class WebSearcher:

    def __init__(self, google_api_key="", google_cse_id="", bing_api_key=""):
        self.google_key = google_api_key
        self.google_cse = google_cse_id
        self.bing_key = bing_api_key

    def _rate(self, url):
        u = url.lower()
        for d in [".edu", ".gov", "pubmed", "arxiv.org", "nature.com", "science.org"]:
            if d in u:
                return "S"
        for d in ["reuters.com", "bbc.com", "who.int", "wikipedia.org"]:
            if d in u:
                return "A"
        for d in ["nytimes.com", "wsj.com", "researchgate.net"]:
            if d in u:
                return "B"
        return "C"

    def _ddg(self, query, num=5):
        try:
            r = requests.get("https://api.duckduckgo.com/", params={
                "q": query, "format": "json", "no_html": 1, "skip_disambig": 1
            }, timeout=10)
            r.raise_for_status()
            d = r.json()
            out = []
            for t in d.get("RelatedTopics", [])[:num]:
                if isinstance(t, dict) and "Text" in t:
                    out.append(SearchResult(
                        title=t.get("FirstURL", ""), url=t.get("FirstURL", ""),
                        snippet=t.get("Text", "")[:300], credibility=self._rate(t.get("FirstURL", ""))
                    ))
            if d.get("AbstractText"):
                out.append(SearchResult(
                    title=d.get("AbstractSource", ""), url=d.get("AbstractURL", ""),
                    snippet=d.get("AbstractText", "")[:300], credibility=self._rate(d.get("AbstractURL", ""))
                ))
            return out
        except Exception:
            return []

    def _google(self, query, num=5):
        if not self.google_key or not self.google_cse:
            return []
        try:
            r = requests.get("https://www.googleapis.com/customsearch/v1", params={
                "key": self.google_key, "cx": self.google_cse, "q": query, "num": min(num, 10)
            }, timeout=15)
            r.raise_for_status()
            out = []
            for i in r.json().get("items", []):
                out.append(SearchResult(
                    title=i.get("title", ""), url=i.get("link", ""),
                    snippet=i.get("snippet", ""), credibility=self._rate(i.get("link", ""))
                ))
            return out
        except Exception:
            return []

    def _bing(self, query, num=5):
        if not self.bing_key:
            return []
        try:
            r = requests.get("https://api.bing.microsoft.com/v7.0/search", headers={
                "Ocp-Apim-Subscription-Key": self.bing_key
            }, params={"q": query, "count": num, "mkt": "zh-CN"}, timeout=15)
            r.raise_for_status()
            out = []
            for i in r.json().get("webPages", {}).get("value", []):
                out.append(SearchResult(
                    title=i.get("name", ""), url=i.get("url", ""),
                    snippet=i.get("snippet", ""), credibility=self._rate(i.get("url", ""))
                ))
            return out
        except Exception:
            return []

    def _search_one(self, query, num=5):
        results = []
        if self.google_key and self.google_cse:
            results.extend(self._google(query, num))
        if self.bing_key:
            results.extend(self._bing(query, num))
        if len(results) < 3:
            results.extend(self._ddg(query, num))
        return results[:num]

    def search(self, queries, num_per_query=5):
        if not queries:
            return {}
        out = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(queries), 5)) as ex:
            futs = {ex.submit(self._search_one, q, num_per_query): q for q in queries}
            for fut in concurrent.futures.as_completed(futs):
                q = futs[fut]
                try:
                    out[q] = fut.result()
                except Exception:
                    out[q] = []
        return out

    def format_results(self, results):
        lines = []
        for query, items in results.items():
            lines.append(f"\n### 搜索: {query}")
            for it in items:
                lines.append(f"- [{it.credibility}] {it.title}")
                lines.append(f"  {it.snippet}")
                lines.append(f"  {it.url}")
        return "\n".join(lines)
