"""
High-Signal Live Web Search & Concurrent Webpage Reader Engine for SLMAgents CPU Harness.

Provides:
- `build_smart_web_query(user_query, document_text)` — extracts domain/role/entity keywords when
  a user asks an open-ended review/improvement/benchmark question over an attached document.
- `live_web_search(query, max_results=5, fetch_top_pages=2)` — queries DuckDuckGo HTML endpoint
  (with `duckduckgo_search` fallback), unwraps redirect URLs, and concurrently extracts clean
  substantive text from top result pages for grounded `[1]..[N]` web citations.
"""

from __future__ import annotations

import concurrent.futures
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..config import get_config
from ..governance.privacy import scrub_secrets_and_pii

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_WEB_INTENT_RE = re.compile(
    r"\b(web\s*search|websearch|search\s+the\s+web|search\s+online|google|duckduckgo|internet|"
    r"latest|current|2025|2026|benchmark|industry\s+standard|market|trend|compare|"
    r"improve|improvement|critique|review|feedback|audit|resume|cv|best\s+practice)\b",
    re.IGNORECASE,
)

_FILLER_RE = re.compile(
    r"\b(can\s+you\s+tell\s+me|please|tell\s+me|what\s+can\s+i|what\s+in\s+this|what\s+in\s+thus|"
    r"i\s+want\s+to\s+use|websearch\s+also|web\s+search\s+also|not\s+like\s+this|think\s+hard|"
    r"give\s+best\s+outcome|in\s+my|attached|uploaded|file|document)\b",
    re.IGNORECASE,
)


_LOCAL_ONLY_EXPORT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:convert|export|download|down\s*load|save|give\s+it|give\s+me|make\s+it|turn\s+it|format\s+it)\b",
    re.IGNORECASE,
)


def should_run_web_search(
    user_query: str,
    explicit_web_search: bool = False,
    has_attached_docs: bool = False,
) -> bool:
    """Determines whether live web search should augment the current turn."""
    if get_config().offline_only:
        return False
    q = (user_query or "").strip()
    q_low = q.lower()
    # When the user attaches a local document (like a CV/resume) or asks for a local export,
    # only run web search if the user explicitly mentions web/search/online in their message.
    if has_attached_docs and not any(
        w in q_low for w in ("web", "internet", "online", "search", "google", "duckduckgo")
    ):
        return False
    if _LOCAL_ONLY_EXPORT_RE.search(q) and not any(
        w in q_low for w in ("web", "internet", "online", "search")
    ):
        return False
    if explicit_web_search:
        return True
    if _WEB_INTENT_RE.search(q):
        return True
    return False


def build_smart_web_query(user_query: str, document_text: str = "", doc_name: str = "") -> str:
    """
    Synthesizes a high-precision web search query. If the user asks a generic critique/improvement
    question (e.g. 'what can I improve in this resume') with an attached document, extracts the
    candidate's target role & technical domain from the document header to retrieve targeted 2026
    industry benchmarks.
    """
    q_raw = (user_query or "").strip()
    q_lower = q_raw.lower()
    doc_lower = (document_text or "")[:2000].lower()
    name_lower = (doc_name or "").lower()

    is_resume_or_cv = (
        any(k in q_lower for k in ("resume", "cv", "profile", "career"))
        or any(k in name_lower for k in ("cv", "resume", "bio"))
        or ("experience" in doc_lower and ("education" in doc_lower or "skills" in doc_lower))
    )

    is_critique_or_improve = any(
        k in q_lower for k in ("improve", "review", "critique", "feedback", "better", "upgrade", "rewrite", "strengthen")
    )

    if is_resume_or_cv and (is_critique_or_improve or len(q_raw.split()) <= 12):
        # Extract role & domain keywords from the top of the resume
        role_terms: List[str] = []
        for candidate_role in (
            "Principal Architect",
            "Solution Architect",
            "Staff Engineer",
            "VP of Technology",
            "Engineering Manager",
            "Generative AI",
            "Agentic AI",
            "LLM",
            "Machine Learning",
            "Data Scientist",
            "Full Stack",
            "Backend Engineer",
        ):
            if candidate_role.lower() in doc_lower:
                role_terms.append(candidate_role)
                if len(role_terms) >= 3:
                    break
        role_prefix = " ".join(role_terms) if role_terms else "Senior AI Software Architect"
        return f"{role_prefix} resume best practices executive impact metrics 2026"

    # Strip conversational filler for general queries
    cleaned = _FILLER_RE.sub(" ", q_raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?!.,;:-")
    if len(cleaned) < 6 and document_text:
        head_words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", document_text[:400])[:8]
        cleaned = f"{' '.join(head_words)} {q_raw}".strip()
    return scrub_secrets_and_pii(cleaned or q_raw, scrub_pii=True)[:180]


def _unwrap_ddg_url(href: str) -> str:
    if not href:
        return ""
    if "uddg=" in href:
        try:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs and qs["uddg"]:
                return urllib.parse.unquote(qs["uddg"][0])
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _fetch_page_excerpt(url: str, max_chars: int = 500, timeout_sec: float = 2.0) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "pdf" in ctype or "image" in ctype or "octet-stream" in ctype:
                return ""
            raw = resp.read(65_000)
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "form", "svg"]):
            tag.decompose()
        paras: List[str] = []
        for p in soup.find_all(["p", "li", "h2", "h3"]):
            txt = p.get_text(" ", strip=True)
            if len(txt) >= 45 and not txt.lower().startswith(("cookie", "accept all", "sign in", "subscribe")):
                paras.append(txt)
                if sum(len(x) for x in paras) >= max_chars:
                    break
        joined = " ".join(paras).strip()
        return joined[:max_chars]
    except Exception:
        return ""


def live_web_search(
    query: str,
    max_results: int = 4,
    fetch_top_pages: int = 1,
    timeout_sec: float = 3.0,
) -> List[Dict[str, Any]]:
    """
    Executes a live DuckDuckGo HTML search (with `duckduckgo_search` fallback) and enriches the
    top results with extracted webpage text for high-signal grounding. Guaranteed non-blocking.
    """
    if get_config().offline_only:
        return []

    clean_q = scrub_secrets_and_pii((query or "").strip(), scrub_pii=True)
    if not clean_q:
        return []

    raw_hits: List[Dict[str, str]] = []

    # Primary: DuckDuckGo HTML endpoint (high relevance, zero API key required)
    try:
        data = urllib.parse.urlencode({"q": clean_q}).encode("utf-8")
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            html = resp.read(120_000).decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select(".result"):
            a_el = node.select_one(".result__a")
            snip_el = node.select_one(".result__snippet")
            if not a_el:
                continue
            title = a_el.get_text(" ", strip=True)
            url = _unwrap_ddg_url(a_el.get("href", ""))
            snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
            if title and url and not url.startswith("https://duckduckgo.com/y.js"):
                raw_hits.append({"title": title, "url": url, "snippet": snippet})
                if len(raw_hits) >= max_results:
                    break
    except Exception:
        pass

    # Fallback: duckduckgo_search package if HTML endpoint was blocked/empty
    if not raw_hits:
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                for item in ddgs.text(clean_q, max_results=max_results):
                    title = str(item.get("title") or "").strip()
                    url = str(item.get("href") or item.get("url") or "").strip()
                    snippet = str(item.get("body") or item.get("snippet") or "").strip()
                    if title and url:
                        raw_hits.append({"title": title, "url": url, "snippet": snippet})
        except Exception:
            pass

    if not raw_hits:
        return []

    # Concurrently fetch clean page excerpts with strict non-blocking shutdown
    excerpts: Dict[int, str] = {}
    if fetch_top_pages > 0:
        target_indices = list(range(min(len(raw_hits), fetch_top_pages)))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(target_indices)))
        try:
            futs = {
                pool.submit(_fetch_page_excerpt, raw_hits[i]["url"]): i
                for i in target_indices
            }
            for fut in concurrent.futures.as_completed(futs, timeout=2.2):
                idx = futs[fut]
                try:
                    excerpts[idx] = fut.result(timeout=0.1) or ""
                except Exception:
                    excerpts[idx] = ""
        except Exception:
            pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    evidence: List[Dict[str, Any]] = []
    for idx, hit in enumerate(raw_hits):
        extra = excerpts.get(idx, "")
        body = hit["snippet"]
        if extra and extra not in body:
            body = f"{body}\nKey Page Excerpt: {extra}".strip()
        domain = ""
        try:
            domain = urllib.parse.urlparse(hit["url"]).netloc.replace("www.", "")
        except Exception:
            domain = hit["url"]
        evidence.append(
            {
                "source": f"Web: {hit['title'][:68]} ({domain})",
                "location": hit["url"],
                "url": hit["url"],
                "title": hit["title"],
                "text": body,
                "score": round(0.96 - idx * 0.03, 3),
                "collection": "live_web",
            }
        )
    return evidence
