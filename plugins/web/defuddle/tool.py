"""``web_research`` — one call that searches the web and returns cleaned page bodies.

The two-step dance (``web_search`` to find candidates, then ``web_extract`` to read them)
costs a round trip and invites the failure it was meant to prevent: a model that reads the
search snippets, decides they look sufficient, and answers without opening anything. This
tool removes the choice — searching *is* fetching.

It composes the built-in tools rather than reimplementing them, so the TTL cache, per-page
char budget, secret-URL refusal, private-network blocking and one-shot keyless rescue all
still apply. Page bodies come from the configured ``web.extract_backend``, which this repo
pins to Defuddle — so what comes back is article text, not nav and cookie banners.

Returns JSON::

    {"query": ..., "searched": N, "fetched": M, "results": [
        {"position", "title", "url", "description", "content", "error"}, ...]}

A page that fails to fetch keeps its search metadata and carries an ``error``, so a partial
failure degrades to "snippet only" for that one result instead of failing the call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fetching is the expensive half, so search wider than we read: the extra hits still come
# back as titled snippets and let the model say "none of these are worth opening".
_DEFAULT_SEARCH_LIMIT = 5
_DEFAULT_MAX_PAGES = 3
# web_extract's own tool schema caps a single call at 5 URLs; stay inside it.
_HARD_PAGE_CAP = 5
# Below web.extract_char_limit (15000): that budget assumes ONE page, and this tool returns
# several into a 65k-token context. Three pages at 6000 is ~4.5k tokens, which is affordable.
_DEFAULT_CHAR_LIMIT = 6000

WEB_RESEARCH_SCHEMA = {
    "name": "web_research",
    "description": (
        "Search the web AND read the top results in one call. Returns each hit with its title, URL, "
        "snippet, and the actual page body as clean markdown (main content only — nav, sidebars, "
        "cookie banners and footers stripped). Prefer this over web_search whenever you intend to "
        "rely on, quote, or cite what you find: a search snippet is a lead, not a source. Use plain "
        "web_search only when you genuinely want a list of links without reading them, and web_extract "
        "when you already know the URL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The search query. Backend-supported operators such as site:example.com, "
                    "filetype:pdf, intitle:word, -term or \"exact phrase\" work here."
                ),
            },
            "max_pages": {
                "type": "integer",
                "description": (
                    f"How many of the top results to actually fetch and clean. Defaults to "
                    f"{_DEFAULT_MAX_PAGES}, max {_HARD_PAGE_CAP}. Remaining hits come back as "
                    "snippet-only entries."
                ),
                "minimum": 1,
                "maximum": _HARD_PAGE_CAP,
                "default": _DEFAULT_MAX_PAGES,
            },
            "search_limit": {
                "type": "integer",
                "description": (
                    f"How many search hits to list before fetching. Defaults to {_DEFAULT_SEARCH_LIMIT}."
                ),
                "minimum": 1,
                "maximum": 20,
                "default": _DEFAULT_SEARCH_LIMIT,
            },
            "char_limit": {
                "type": "integer",
                "description": (
                    f"Per-page character budget (default {_DEFAULT_CHAR_LIMIT}). Longer pages are "
                    "head+tail truncated with a footer naming the file holding the full text."
                ),
                "minimum": 2000,
            },
        },
        "required": ["query"],
    },
}


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


def _error(query: str, message: str) -> str:
    return json.dumps({"query": query, "searched": 0, "fetched": 0, "error": message, "results": []})


def _search_hits(query: str, limit: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Run the configured search backend; returns ``(hits, error)``."""
    from tools.web_tools import web_search_tool

    try:
        payload = json.loads(web_search_tool(query, limit=limit))
    except (json.JSONDecodeError, TypeError) as exc:
        return [], f"web_search returned malformed JSON: {exc}"
    if not payload.get("success", False):
        return [], str(payload.get("error") or "web search failed")
    hits = ((payload.get("data") or {}).get("web")) or []
    return [h for h in hits if isinstance(h, dict)], None


async def _fetch_bodies(urls: List[str], char_limit: int) -> Dict[str, Dict[str, Any]]:
    """Extract *urls* through the configured extract backend, keyed by URL."""
    from tools.web_tools import web_extract_tool

    try:
        payload = json.loads(await web_extract_tool(urls, "markdown", char_limit=char_limit))
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("web_research: extract returned malformed JSON: %s", exc)
        return {}
    by_url: Dict[str, Dict[str, Any]] = {}
    for entry in payload.get("results") or []:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("metadata")
        source = meta.get("sourceURL") if isinstance(meta, dict) else None
        # Key on the REQUESTED url when the backend reports a redirect target as `url`.
        for key in (entry.get("url"), source):
            if isinstance(key, str) and key:
                by_url.setdefault(key, entry)
    return by_url


async def web_research_tool(
    query: str,
    max_pages: int = _DEFAULT_MAX_PAGES,
    search_limit: int = _DEFAULT_SEARCH_LIMIT,
    char_limit: Optional[int] = None,
) -> str:
    """Search, then read the top hits. See module docstring for the response shape."""
    query = (query or "").strip()
    if not query:
        return _error(query, "query is required")

    search_limit = _as_int(search_limit, _DEFAULT_SEARCH_LIMIT, 1, 20)
    max_pages = min(_as_int(max_pages, _DEFAULT_MAX_PAGES, 1, _HARD_PAGE_CAP), search_limit)
    budget = _as_int(char_limit, _DEFAULT_CHAR_LIMIT, 2000, 100_000)

    hits, error = _search_hits(query, search_limit)
    if error is not None:
        return _error(query, error)
    if not hits:
        return json.dumps({"query": query, "searched": 0, "fetched": 0, "results": []})

    wanted = [str(h.get("url") or "") for h in hits[:max_pages]]
    wanted = [u for u in wanted if u]
    bodies = await _fetch_bodies(wanted, budget) if wanted else {}
    logger.info("web_research '%s': %d hit(s), fetched %d", query, len(hits), len(bodies))

    results = []
    for index, hit in enumerate(hits):
        url = str(hit.get("url") or "")
        entry: Dict[str, Any] = {
            "position": hit.get("position", index + 1),
            "title": hit.get("title", ""),
            "url": url,
            "description": hit.get("description", ""),
        }
        fetched = bodies.get(url)
        if fetched is not None:
            content = fetched.get("content") or fetched.get("raw_content") or ""
            if content:
                entry["content"] = content
            if fetched.get("error"):
                entry["error"] = str(fetched["error"])
            elif not content:
                entry["error"] = "no content extracted"
        elif url in wanted:
            entry["error"] = "page was not returned by the extract backend"
        results.append(entry)

    return json.dumps({
        "query": query,
        "searched": len(results),
        "fetched": sum(1 for r in results if r.get("content")),
        "results": results,
    })


def register_tool(ctx) -> None:
    """Attach ``web_research`` to the ``web`` toolset, gated on a web backend existing."""
    from tools.web_tools import check_web_api_key

    ctx.register_tool(
        name="web_research",
        toolset="web",
        schema=WEB_RESEARCH_SCHEMA,
        handler=lambda args, **kw: web_research_tool(
            args.get("query", ""),
            max_pages=args.get("max_pages", _DEFAULT_MAX_PAGES),
            search_limit=args.get("search_limit", _DEFAULT_SEARCH_LIMIT),
            char_limit=args.get("char_limit"),
        ),
        check_fn=check_web_api_key,
        is_async=True,
        description="Search the web and read the top results as clean markdown in one call.",
        emoji="🔬",
    )
