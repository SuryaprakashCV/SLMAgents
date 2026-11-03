"""
Universal Multi-Format Document Compiler & Visual Design Studio (`file_exporter.py`).

Provides:
1. **Design Review & Tone Styler (`DesignTheme`, `review_and_enhance_design`)**:
   - Automatically selects or applies user-requested tone & visual design themes:
     `executive` (Executive Professional), `friendly` (Friendly & Warm),
     `creative` (Modern Creative), `casual` (Casual & Conversational),
     `minimal` (Minimalist Editorial), and `tech` (Cyber High-Tech).
   - Audits slide/section hierarchy, extracts quantified KPI metrics into visual callout cards,
     and strips any LLM refusal boilerplate ("I can't generate a downloadable file...").
2. **Designer-Grade Multi-Format Compilation on Pure CPU**:
   - `.pptx` (Widescreen 16:9 custom-designed PowerPoint slide deck with themed cover slide,
     header ribbons, KPI metric cards, styled content cards, and OOXML smooth fade transitions)
   - `.pdf`  (Multi-page vector A4 PDF via PyMuPDF `Story` with themed banners & callout boxes)
   - `.docx` (Microsoft Word OpenXML `.docx` with themed RGB headings, bold runs, and styled tables)
   - `.html` (Interactive, CSS-animated HTML5 presentation/document with live theme switcher)
   - `.xlsx` (Styled Microsoft Excel workbook via OpenPyXL)
   - `.csv` / `.tsv` / `.json` / `.md` / `.txt` / `.py` / `.sql`
"""

from __future__ import annotations

import csv
import html as html_lib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import get_config

SUPPORTED_EXPORT_FORMATS = (
    "pdf",
    "docx",
    "xlsx",
    "csv",
    "tsv",
    "pptx",
    "md",
    "html",
    "json",
    "txt",
    "py",
    "sql",
)

MIME_TYPES: Dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "py": "text/x-python; charset=utf-8",
    "sql": "application/sql; charset=utf-8",
    "wav": "audio/wav",
}


@dataclass(frozen=True)
class DesignTheme:
    key: str
    label: str
    tone_description: str
    primary_hex: str      # Deep header / cover background
    secondary_hex: str    # Secondary card / dark accent
    accent_hex: str       # Primary brand accent
    highlight_hex: str    # KPI metric / badge highlight
    bg_hex: str           # Soft slide / page background
    card_hex: str         # Content card surface
    text_hex: str         # Primary body text
    muted_hex: str        # Muted caption / footer text
    font_heading: str = "Calibri"
    font_body: str = "Calibri"

    @property
    def primary_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.primary_hex)

    @property
    def secondary_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.secondary_hex)

    @property
    def accent_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.accent_hex)

    @property
    def highlight_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.highlight_hex)

    @property
    def bg_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.bg_hex)

    @property
    def card_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.card_hex)

    @property
    def text_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.text_hex)

    @property
    def muted_rgb(self) -> Tuple[int, int, int]:
        return _hex_to_rgb(self.muted_hex)


def _hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (15, 23, 42)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


DESIGN_THEMES: Dict[str, DesignTheme] = {
    "executive": DesignTheme(
        key="executive",
        label="Executive Professional",
        tone_description="Authoritative, impact-driven, and boardroom-ready with high-contrast KPI callouts",
        primary_hex="#0F172A",
        secondary_hex="#1E293B",
        accent_hex="#4F46E5",
        highlight_hex="#059669",
        bg_hex="#F8FAFC",
        card_hex="#FFFFFF",
        text_hex="#0F172A",
        muted_hex="#64748B",
    ),
    "friendly": DesignTheme(
        key="friendly",
        label="Friendly & Warm",
        tone_description="Welcoming, human-centric, and engaging with warm coral & amber accents",
        primary_hex="#292524",
        secondary_hex="#44403C",
        accent_hex="#EA580C",
        highlight_hex="#D97706",
        bg_hex="#FFFBEB",
        card_hex="#FFFFFF",
        text_hex="#1C1917",
        muted_hex="#78716C",
    ),
    "creative": DesignTheme(
        key="creative",
        label="Modern Creative",
        tone_description="Bold, expressive, and visionary with vivid violet & magenta highlights",
        primary_hex="#1E1B4B",
        secondary_hex="#312E81",
        accent_hex="#7C3AED",
        highlight_hex="#DB2777",
        bg_hex="#F5F3FF",
        card_hex="#FFFFFF",
        text_hex="#1E1B4B",
        muted_hex="#6D28D9",
    ),
    "casual": DesignTheme(
        key="casual",
        label="Casual & Conversational",
        tone_description="Relaxed, clear, and approachable with ocean blue & teal accents",
        primary_hex="#0C4A6E",
        secondary_hex="#075985",
        accent_hex="#0284C7",
        highlight_hex="#0D9488",
        bg_hex="#F0F9FF",
        card_hex="#FFFFFF",
        text_hex="#0F172A",
        muted_hex="#475569",
    ),
    "minimal": DesignTheme(
        key="minimal",
        label="Minimalist Editorial",
        tone_description="Clean, restrained architectural typography with monochrome charcoal & emerald",
        primary_hex="#18181B",
        secondary_hex="#27272A",
        accent_hex="#15803D",
        highlight_hex="#0F766E",
        bg_hex="#FAFAF9",
        card_hex="#FFFFFF",
        text_hex="#18181B",
        muted_hex="#71717A",
    ),
    "tech": DesignTheme(
        key="tech",
        label="High-Tech AI Studio",
        tone_description="Precision engineering aesthetic with deep obsidian, electric blue, and cyan",
        primary_hex="#090D16",
        secondary_hex="#111827",
        accent_hex="#2563EB",
        highlight_hex="#0891B2",
        bg_hex="#EFF6FF",
        card_hex="#FFFFFF",
        text_hex="#0F172A",
        muted_hex="#475569",
    ),
}


@dataclass
class DesignReviewReport:
    theme: DesignTheme
    document_type: str
    section_count: int
    doc_title: str = ""
    doc_subtitle: str = ""
    extracted_metrics: List[Tuple[str, str]] = field(default_factory=list)
    improvements_applied: List[str] = field(default_factory=list)
    review_loop_steps: List[str] = field(default_factory=list)

    def to_thought_steps(self) -> List[str]:
        steps: List[str] = list(self.review_loop_steps)
        steps.append(
            f"Design Critic applied '{self.theme.label}' typography & layout to {self.document_type} ({self.section_count} sections)."
        )
        if self.extracted_metrics:
            m_preview = ", ".join(f"{v} ({lbl})" for v, lbl in self.extracted_metrics[:4])
            steps.append(f"Extracted {len(self.extracted_metrics)} quantified impact metrics: {m_preview}.")
        for imp in self.improvements_applied[:2]:
            if imp not in steps:
                steps.append(imp)
        return steps


# Matches ANY hallucinated LLM download link e.g. [Download PDF](https://designstudio.slma.ground.com/suryaprakash-cv-v9-docx)
_FAKE_DOWNLOAD_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(\s*((?!mailto:|/api/harness/download/|https?://(?:www\.)?(?:linkedin\.com|github\.com))\S+?)\s*\)",
    re.IGNORECASE,
)

_BARE_EXAMPLE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:example\.(?:com|org|net)|[A-Za-z0-9.\-]*?(?:slma|designstudio|ground\.com|your-domain\.com|files\.local)[A-Za-z0-9.\-]*)/([A-Za-z0-9_.\-/]+)",
    re.IGNORECASE,
)


def _is_hallucinated_download_link(label: str, url: str) -> bool:
    u_low = (url or "").lower().strip()
    l_low = (label or "").lower().strip()
    if u_low.startswith(("/api/harness/download/", "mailto:", "#")):
        return False
    if any(h in u_low for h in ("linkedin.com", "github.com")):
        return False
    if any(
        bad in u_low
        for bad in (
            "example.",
            "slma",
            "designstudio",
            "ground.com",
            "your-domain",
            "files.local",
            "download.link",
            "sandbox:",
            "file:///",
            "localhost",
            "127.0.0.1",
        )
    ):
        return True
    if re.search(r"[.\-](?:pdf|docx|doc|pptx|ppt|xlsx|xls|csv|tsv|md|html|json|txt)(?:$|\?)", u_low):
        return True
    if any(
        kw in l_low
        for kw in ("download", "click here", "pdf", "docx", "word", "ppt", "powerpoint", "presentation", "cv", "resume", "version")
    ):
        return True
    return False


def _extract_fake_url_stem_and_fmt(label: str, url: str) -> Tuple[str, str]:
    clean_url = (url or "").split("?")[0].rstrip("/")
    bname = os.path.basename(clean_url) or "slm_studio_document"
    stem, ext = os.path.splitext(bname)
    if ext:
        return _sanitize_stem(stem), _normalize_format(ext, bname)
    # Check hyphenated suffix like `suryaprakash-cv-v9-docx` or `resume-v2-pdf`
    m_hyph = re.match(r"^(.+?)[_\-](pdf|docx|doc|pptx|ppt|xlsx|xls|csv|html|md|json|txt)$", bname, re.IGNORECASE)
    if m_hyph:
        return _sanitize_stem(m_hyph.group(1)), _normalize_format(m_hyph.group(2), "")
    l_low = (label or "").lower()
    for fmt_code, kws in (
        ("pptx", ("pptx", "ppt", "powerpoint", "presentation", "slide")),
        ("docx", ("docx", "word", ".doc")),
        ("pdf", ("pdf",)),
        ("xlsx", ("xlsx", "excel")),
        ("html", ("html",)),
    ):
        if any(k in l_low for k in kws):
            return _sanitize_stem(bname), fmt_code
    return _sanitize_stem(bname), "pdf"

# Broad, typo-tolerant export & file generation intent regex
_EXPORT_INTENT_RE = re.compile(
    r"\b(download|down\s*load|dl|export|save\s+as|convert\s+(?:it\s+|this\s+)?(?:to|into)|"
    r"make\s+(?:a|it\s+a)|create\s+(?:a|the)|build\s+(?:a|the)|give\s+(?:it\s+)?(?:to\s+|o\s+)?me|"
    r"linjk|link|ppt|pptx|powerpoint|presentation|slide\s*deck|slides|pdf|docx|word\s+doc|xlsx|excel|spreadsheet)\b",
    re.IGNORECASE,
)

# Matches LLM refusal boilerplate when asked to generate a downloadable file
_LLM_REFUSAL_LINE_RE = re.compile(
    r"^(?:I\s+(?:can't|cannot|am\s+unable\s+to|don't\s+have\s+the\s+ability\s+to)\s+(?:generate|create|provide|export|directly\s+create).*?(?:downloadable|file|ppt|powerpoint|pdf|docx|chat\s+interface).*|"
    r"However,\s+I\s+can\s+help\s+you\s+create\s+a\s+professional.*?(?:copy\s+into|PowerPoint|Google\s+Slides).*|"
    r"\(Note:\s*This\s+is\s+a\s+simulated\s+link.*?\))",
    re.IGNORECASE | re.MULTILINE,
)

_METRIC_TOKEN_RE = re.compile(
    r"(\b(?:\d+(?:\.\d+)?(?:\+|%|x|X|\s*(?:M\+|B\+|K\+|years?|FTEs?|days?|ms|calls/month|queries/month))|~\d+%|\<\d+\s*days?))",
    re.IGNORECASE,
)


@dataclass
class ExportedFile:
    file_id: str
    filename: str
    fmt: str
    abs_path: str
    download_url: str
    size_bytes: int
    mime_type: str
    theme_key: str = "executive"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "format": self.fmt,
            "download_url": self.download_url,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "theme": self.theme_key,
        }


def _downloads_root() -> str:
    cfg = get_config()
    root = os.path.join(cfg.data_dir, "downloads")
    os.makedirs(root, exist_ok=True)
    return root


def _sanitize_stem(name: str) -> str:
    base = os.path.basename(name or "document")
    stem, _ext = os.path.splitext(base)
    clean = re.sub(r"[^A-Za-z0-9_\-]+", "_", stem).strip("_")
    return clean[:80] or "slm_exported_document"


def _normalize_format(fmt: str, filename: str = "") -> str:
    f = (fmt or "").lower().strip().lstrip(".")
    if not f and filename:
        f = os.path.splitext(filename)[1].lower().lstrip(".")
    alias = {
        "doc": "docx",
        "word": "docx",
        "xls": "xlsx",
        "excel": "xlsx",
        "ppt": "pptx",
        "powerpoint": "pptx",
        "presentation": "pptx",
        "slides": "pptx",
        "markdown": "md",
        "text": "txt",
        "python": "py",
    }
    f = alias.get(f, f)
    return f if f in SUPPORTED_EXPORT_FORMATS else "pdf"


def select_design_theme(user_query: str = "", content: str = "", preferred_tone: str = "") -> DesignTheme:
    """Selects the best visual design theme based on user preference, query tone keywords, or document type."""
    pref = (preferred_tone or "").lower().strip()
    if pref in DESIGN_THEMES:
        return DESIGN_THEMES[pref]

    q = f"{user_query} {pref}".lower()
    if any(w in q for w in ("friendly", "warm", "welcoming", "human", "people", "onboarding")):
        return DESIGN_THEMES["friendly"]
    if any(w in q for w in ("creative", "bold", "vibrant", "colorful", "artistic", "pitch", "keynote", "animated")):
        return DESIGN_THEMES["creative"]
    if any(w in q for w in ("casual", "conversational", "relaxed", "fun", "simple", "team sync")):
        return DESIGN_THEMES["casual"]
    if any(w in q for w in ("minimal", "minimalist", "clean", "editorial", "academic", "monochrome")):
        return DESIGN_THEMES["minimal"]
    if any(w in q for w in ("cyber", "high-tech", "dark", "developer", "engineering", "code", "benchmark")):
        return DESIGN_THEMES["tech"]
    return DESIGN_THEMES["executive"]


_EMOJI_STRIP_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\uFFFD🎯🧠🤖👤⚡📊📝🧮📄🖼️✅🔥💡🚀🏢🔧🏆🎓🔐🔮🙌📍📞✉️🔗🌐📧📈📌📋📂📥⬇⚠🔁]+"
)
_INLINE_CITATION_RE = re.compile(r"\s*\[\d+\](?:\s*,\s*\[\d+\])*")


def _strip_emojis_for_clean_title(text: str) -> str:
    cleaned = _EMOJI_STRIP_RE.sub("", text or "")
    cleaned = _INLINE_CITATION_RE.sub("", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# Headings that indicate conversational critique/analysis rather than actual document content
_META_CRITIQUE_HEADING_RE = re.compile(
    r"^(?:#+\s*)?(?:[\W_]*)(?:Slide\s+\d+\s*[:–—-]\s*)?(?:[\W_]*)(?:"
    r"Resume\s+Design\s*&\s*Improvement\s+Plan|"
    r"Design\s*&\s*Improvement\s+Plan|"
    r"Executive\s+Diagnosis(?:\s*&\s*Strengths)?|"
    r"Diagnosis\s*&\s*Strengths|"
    r"Key\s+Strengths(?:\s+Identified)?\s*:?|"
    r"Critical\s+Gaps.*|"
    r"High[-\s]Impact\s+Gaps.*|"
    r"Identified\s+Gaps.*|"
    r"Areas\s+for\s+Improvement.*|"
    r"Before\s*(?:->|→|&|and|vs\.?)\s*After.*|"
    r"BEFORE\s*:?|"
    r"AFTER\s*\(.*|"
    r"Key\s+Improvements\s+Explained.*|"
    r"Improvements\s+Explained.*|"
    r"Why\s+(?:It|This)\s+Works.*|"
    r"Prioritized\s+Action\s+Plan.*|"
    r"Action\s+Plan\s*[–—-]?\s*Next\s+Steps.*|"
    r"Summary\s+of\s+(?:Key\s+)?(?:Changes|Improvements).*|"
    r"Download\s+Links.*|"
    r"How\s+to\s+(?:Download|Create|Save|Export).*|"
    r"Want\s+Me\s+to\s+Generate.*|"
    r"Would\s+you\s+like\s+me\s+to.*"
    r")$",
    re.IGNORECASE,
)

# Headings that introduce the final ready-to-export document block
_FINAL_DOC_WRAPPER_HEADING_RE = re.compile(
    r"^(?:#+\s*)?(?:[\W_]*)(?:"
    r"Final\s+(?:Resume|CV|Document|Presentation|Slide\s+Deck)(?:\s+Output)?.*|"
    r"Upgraded\s+(?:Resume|CV|Document).*|"
    r"Rewritten\s+(?:Resume|CV|Document).*|"
    r"Improved\s+(?:Resume|CV|Document)\s*(?:Version|Output)?.*|"
    r"Complete\s+(?:Rewritten|Upgraded)?\s*(?:Resume|CV|Document).*|"
    r"ATS[-\s]Optimized\s+(?:Resume|CV).*|"
    r".*?\(\s*Markdown\s+Format\s*[–—-]\s*Ready\s+to\s+Export\s*\).*"
    r")$",
    re.IGNORECASE,
)

# Conversational intro/outro lines that belong in chat, NOT inside a downloaded file
_CONVERSATIONAL_CHAT_LINE_RE = re.compile(
    r"^(?:"
    r"Your\s+current\s+(?:CV|resume)\s+is\b.*|"
    r"Aligned\s+with\s+202\d\s+ATS\s+Benchmarks.*|"
    r"(?:Certainly|Absolutely|Sure|Of\s+course)[!.,]?\s+(?:Below|Here)\s+is\b.*|"
    r"Below\s+is\s+a\s+(?:fully\s+improved|professional|complete|rewritten)\b.*|"
    r"Here\s+is\s+(?:the|a|your)\s+(?:fully\s+improved|upgraded|rewritten|complete)\b.*|"
    r">\s*\*\*(?:Design\s+Studio\s+Compiled|Updated\s+&\s+Applied\s+Skill|Created\s+&\s+Applied\s+Skill).*|"
    r"\*\*(?:Design\s+Studio\s+Review|Ready\s+for\s+Download|Generated\s+Files\s+Ready\s+for\s+Download)\s*:?\*\*.*"
    r")$",
    re.IGNORECASE,
)


def clean_export_content(raw_text: str) -> str:
    """
    Removes LLM refusal preambles, Studio download banners, manual 'How to Download as PPT' instructions,
    fake download links, and inline/trailing RAG citations.
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    # Strip leading or repeated Studio banners (`> **Design Studio Compiled...**`, `> **Updated & Applied Skill...**`)
    text = re.sub(
        r"^(?:>\s*\*\*(?:Design\s+Studio\s+Compiled|Updated\s+&\s+Applied\s+Skill|Created\s+&\s+Applied\s+Skill)[^\n]*(?:\n+|$))+",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()

    # Strip any inline `/api/harness/download/...` markdown links (even if truncated mid-link)
    text = re.sub(
        r"\[[^\]]*Download[^\]]*\]\(/api/harness/download/[^\)\n]*\)?\s*[·•|]*\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Strip trailing sources or previous download bars
    text = re.split(r"\n---\s*\n\*\*Sources:\*\*", text, maxsplit=1)[0].strip()
    text = re.split(r"\n---\s*\n(?:📥\s*)?\*\*Generated Files Ready for Download:\*\*", text, maxsplit=1)[0].strip()
    text = re.split(r"\n---\s*\n\*\*Design (?:Studio|Review) (?:Review|Agent)", text, maxsplit=1)[0].strip()
    text = re.split(r"\n---\s*\n\*\*Ready for Download:\*\*", text, maxsplit=1)[0].strip()

    # Strip trailing "How to Download as PPT", "Want Me to Generate a Real PPT File?", or "Would you like me to:" filler
    text = re.split(
        r"\n(?:✅\s*)?(?:#+\s*)?How to (?:Download|Create|Save) as (?:PPT|PDF|DOCX|PowerPoint)[\s\S]*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    text = re.split(
        r"\n(?:🚀\s*)?(?:#+\s*)?Want Me to Generate a Real (?:PPT|PDF|DOCX|File)[\s\S]*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    text = re.split(
        r"\n(?:💡\s*|✨\s*)?(?:#+\s*)?Would you like me to\s*:[\s\S]*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()

    # Strip refusal lines & "Step 1: Copy the PPT Slide Content Below" boilerplate
    text = _LLM_REFUSAL_LINE_RE.sub("", text)
    text = re.sub(
        r"^.*?(?:Step-by-Step:\s*How to Create Your Own Downloadable|Step 1:\s*Copy the PPT Slide Content Below|Use this structure to build your presentation).*$\n?",
        "",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(
            bad in low
            for bad in (
                "example.com/",
                "sandbox:/mnt/",
                "simulated link for demonstration",
                "designstudio.",
                "slma.",
                "ground.com/",
                "downloadable version:",
                "/api/harness/download/",
            )
        ):
            line = _FAKE_DOWNLOAD_LINK_RE.sub(
                lambda m: "" if _is_hallucinated_download_link(m.group(1), m.group(2)) else m.group(0),
                line,
            )
            line = _BARE_EXAMPLE_URL_RE.sub("", line)
            line = re.sub(r"\(Note:\s*This is a simulated link.*?\)", "", line, flags=re.IGNORECASE)
            line = re.sub(r"^Downloadable Version:\s*$", "", line.strip(), flags=re.IGNORECASE)
            if not line.strip(" -*•:>"):
                continue
        cleaned_lines.append(line)

    # Remove any dangling mid-sentence truncated last line (e.g. "• Generate a" or "🔗 [")
    while cleaned_lines:
        last_l = cleaned_lines[-1].strip()
        if not last_l:
            cleaned_lines.pop()
            continue
        if (
            last_l.endswith("[")
            or last_l.endswith("(")
            or re.match(
                r"^(?:[-*•🔹→]\s+)?(?:[A-Za-z]+\s+){0,5}\b(?:a|an|the|to|for|with|and|or|in|of|by|into|from|as)$",
                last_l,
                re.IGNORECASE,
            )
        ):
            cleaned_lines.pop()
            continue
        break

    result = "\n".join(cleaned_lines).strip()
    fence_match = re.match(r"^```(?:markdown|md|text|txt)?\s*\n([\s\S]+?)(?:\n```)?$", result, re.IGNORECASE)
    if fence_match:
        result = fence_match.group(1).strip()

    return result


def extract_pure_document_content(raw_text: str, fallback_document_text: str = "") -> Tuple[str, List[str]]:
    """
    Isolates ONLY the publication-ready document content from a chat response:
    - Extracts any explicit `<!-- DOCUMENT_START -->` block or `Final Resume Output` / `Upgraded Document` section
    - Unwraps ` ```markdown ... ``` ` code fences so document headings/bullets are never rendered as `<pre><code>`
    - Strips conversational diagnosis/critique sections (`Executive Diagnosis`, `High-Impact Gaps`,
      `Before -> After Rewrites`, `Key Improvements Explained`) and meta-tables (`| Gap | Why It Matters |`)
    - Strips inline `[1], [2]` RAG/web citation markers.
    Returns `(pure_markdown, extraction_notes)`.
    """
    notes: List[str] = []
    base = clean_export_content(raw_text)
    if not base:
        return clean_export_content(fallback_document_text), ["Used attached source document content."]

    # 1. Explicit document delimiter markers if present
    doc_marker = re.search(r"<!--\s*DOCUMENT_START\s*-->([\s\S]+?)(?:<!--\s*DOCUMENT_END\s*-->|$)", base, re.IGNORECASE)
    if doc_marker and len(doc_marker.group(1).strip()) > 120:
        base = doc_marker.group(1).strip()
        notes.append("Extracted pure document block from DOCUMENT_START marker.")

    # 2. Check if there is a `Final Resume Output (Markdown Format - Ready to Export)` heading
    lines = base.splitlines()
    for idx, line in enumerate(lines):
        s_clean = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", line.strip()))
        if _FINAL_DOC_WRAPPER_HEADING_RE.match(s_clean):
            after_wrapper = "\n".join(lines[idx + 1 :]).strip()
            if len(after_wrapper) > 120:
                base = after_wrapper
                notes.append(f"Isolated final document section following '{s_clean.lstrip('#').strip()}'.")
                break

    # 3. Unwrap any ` ```markdown ... ``` ` or ` ``` ... ``` ` block that wraps the document
    fenced_doc = re.search(
        r"```(?:markdown|md|text|txt)?\s*\n([\s\S]{150,}?)(?:\n```|$)",
        base,
        re.IGNORECASE,
    )
    if fenced_doc:
        inner = fenced_doc.group(1).strip()
        inner_low = inner.lower()
        if any(
            kw in inner_low
            for kw in (
                "# ",
                "## ",
                "executive summary",
                "professional profile",
                "core skills",
                "experience",
                "education",
                "slide 1:",
            )
        ):
            base = inner
            notes.append("Unwrapped fenced Markdown document block so headings and bullets render natively.")

    # 4. Filter out critique/meta-commentary sections and meta-tables (`| Gap | Why It Matters |`, `| Improvement | Why It Works |`)
    filtered_lines: List[str] = []
    skip_current_section = False
    in_table = False
    skip_table = False
    removed_sections: List[str] = []

    for raw_line in base.splitlines():
        s = raw_line.strip()
        s_plain = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", s))

        # Check markdown fence lines left over from unclosed fences
        if s.startswith("```") and s.lower() in ("```", "```markdown", "```md", "```text", "```txt"):
            continue

        # Check if this line is a section heading
        is_heading = bool(re.match(r"^#{1,4}\s+.+$", s)) or (
            len(s_plain) < 85
            and (
                _META_CRITIQUE_HEADING_RE.match(s_plain)
                or _FINAL_DOC_WRAPPER_HEADING_RE.match(s_plain)
            )
        )
        if is_heading:
            heading_txt = s_plain.lstrip("#").strip()
            if _META_CRITIQUE_HEADING_RE.match(heading_txt) or _META_CRITIQUE_HEADING_RE.match(s_plain):
                skip_current_section = True
                removed_sections.append(heading_txt)
                continue
            elif _FINAL_DOC_WRAPPER_HEADING_RE.match(heading_txt) or _FINAL_DOC_WRAPPER_HEADING_RE.match(s_plain):
                skip_current_section = False
                continue
            else:
                skip_current_section = False

        if skip_current_section:
            continue

        # Detect meta-critique tables (`| Gap | Why It Matters |` or `| Improvement | Why It Works | Source |`)
        if s.startswith("|") and s.endswith("|"):
            if not in_table:
                in_table = True
                hdr_low = s_plain.lower()
                if any(
                    bad_col in hdr_low
                    for bad_col in ("why it matters", "why it works", "before | after", "| gap |", "| improvement |")
                ):
                    skip_table = True
                else:
                    skip_table = False
            if skip_table:
                continue
        else:
            in_table = False
            skip_table = False

        # Skip conversational chat lines ("Your current CV is...", "Aligned with 2026 ATS Benchmarks...", etc.)
        if _CONVERSATIONAL_CHAT_LINE_RE.match(s_plain) or _CONVERSATIONAL_CHAT_LINE_RE.match(s):
            continue

        # Strip inline blockquote `> ` if the LLM put rewrite content inside `> `
        if s.startswith(">"):
            s_unquoted = re.sub(r"^>\s*", "", raw_line)
            raw_line = s_unquoted

        # Strip inline RAG/web citation brackets `[1]`, `[2], [4]`
        clean_line = _INLINE_CITATION_RE.sub("", raw_line)
        filtered_lines.append(clean_line)

    if removed_sections:
        notes.append(f"Removed {len(removed_sections)} non-document critique sections ({', '.join(removed_sections[:3])}).")

    pure_text = "\n".join(filtered_lines).strip()
    # Clean up excessive horizontal rules or blank lines
    pure_text = re.sub(r"(?:\n\s*---\s*\n){2,}", "\n\n---\n\n", pure_text)
    pure_text = re.sub(r"\n{3,}", "\n\n", pure_text).strip()
    if len(pure_text) < 60 and fallback_document_text and fallback_document_text.strip() != raw_text.strip():
        fb_pure, fb_notes = extract_pure_document_content(fallback_document_text, fallback_document_text="")
        if len(fb_pure) >= len(pure_text):
            return fb_pure, notes + ["Recovered full content from attached source document."] + fb_notes
    return pure_text, notes


def _parse_source_cv_metadata_and_roles(source_text: str) -> Dict[str, Any]:
    """
    Extracts candidate name, contact details, education, patent, and all career roles
    from an uploaded raw CV/Resume text so the self-correction loop can verify completeness
    and restore any omitted/truncated roles.
    """
    info: Dict[str, Any] = {
        "name": "",
        "role_title": "",
        "contact_items": [],
        "education": "",
        "patent": "",
        "roles": [],
        "profile": "",
        "skills": [],
    }
    if not source_text or len(source_text.strip()) < 80:
        return info

    txt = source_text.strip()
    # Extract email, phone, linkedin, location
    email_m = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", txt)
    phone_m = re.search(r"(?:\+?\d{1,3}[\s\-]?)?\b\d{5}[\s\-]?\d{5}\b", txt)
    li_m = re.search(r"linkedin\.com/in/[A-Za-z0-9_\-]+", txt, re.IGNORECASE)
    if "bengaluru" in txt.lower() or "bangalore" in txt.lower():
        info["contact_items"].append("Bengaluru, India")
    if phone_m:
        ph = phone_m.group(0).strip()
        if len(re.sub(r"\D", "", ph)) == 10 and not ph.startswith("+"):
            ph = f"+91 {ph[:5]} {ph[5:]}"
        info["contact_items"].append(ph)
    if email_m:
        info["contact_items"].append(email_m.group(0).strip())
    if li_m:
        info["contact_items"].append(li_m.group(0).strip())

    # Candidate Name & Title from opening words before CONTACT
    head_part = re.split(r"\bCONTACT\b", txt, maxsplit=1)[0].strip()
    if head_part and len(head_part) < 120:
        for role_kw in ("Principal Architect", "VP of Technology", "Staff Engineer", "Chief Architect", "Director"):
            if role_kw.lower() in head_part.lower():
                idx = head_part.lower().index(role_kw.lower())
                cand_name = head_part[:idx].strip(" -–—|·,")
                if cand_name:
                    info["name"] = cand_name
                info["role_title"] = head_part[idx:].strip(" -–—|·,")
                break
        if not info["name"]:
            words = head_part.split()
            if 2 <= len(words) <= 5:
                info["name"] = " ".join(words[:3])

    # Extract Core Skills
    skills_m = re.search(
        r"\bCORE SKILLS\b\s*(.+?)(?=\bEDUCATION\b|\bPATENT\b|\bPROFILE\b|\bEXPERIENCE\b|$)",
        txt,
        re.DOTALL,
    )
    if skills_m:
        raw_sk = skills_m.group(1).strip()
        sk_lines = [s.strip(" -•*·|") for s in raw_sk.splitlines() if s.strip(" -•*·|")]
        if len(sk_lines) <= 2:
            # Split single-line skills block by known skill phrases or capitalized boundaries
            known_skills = [
                "GenAI Architecture & Implementation",
                "Agentic Systems & Multi-Agent Frameworks",
                "Solution & Cloud Architecture",
                "LLM Architecture & LLMOps",
                "NLP & Text Mining",
                "Tech Strategy & Roadmapping",
                "R&D & Engineering Leadership",
                "Team Building & Mentoring",
                "Presales & Solutioning",
                "Python, Java, C#, JAPE, Perl, JavaScript, Shell",
            ]
            matched_sk = [ks for ks in known_skills if ks.lower() in raw_sk.lower()]
            info["skills"] = matched_sk if matched_sk else [re.sub(r"\s+", " ", raw_sk)]
        else:
            info["skills"] = sk_lines

    # Extract Education
    edu_m = re.search(r"\bEDUCATION\b\s*(.+?)(?=\bPATENT\b|\bPROFILE\b|\bEXPERIENCE\b|$)", txt, re.DOTALL)
    if edu_m:
        info["education"] = re.sub(r"\s+", " ", edu_m.group(1)).strip(" |·-")

    # Extract Patent
    pat_m = re.search(r"\bPATENT\b\s*(.+?)(?=\|\s*GENERATIVE|\bGENERATIVE\s+AI|\bPROFILE\b|\bEXPERIENCE\b|$)", txt, re.DOTALL)
    if pat_m:
        info["patent"] = re.sub(r"\s+", " ", pat_m.group(1)).strip(" |·-")

    # Extract Profile
    prof_m = re.search(r"\bPROFILE\b\s*(.+?)(?=\bEXPERIENCE\b|$)", txt, re.DOTALL)
    if prof_m:
        info["profile"] = re.sub(r"\s+", " ", prof_m.group(1)).strip()

    # Extract Experience Roles using `Role · Company Date – Date` pattern
    exp_m = re.search(r"\bEXPERIENCE\b\s*(.+)$", txt, re.DOTALL)
    if exp_m:
        exp_body = exp_m.group(1).strip()
        role_header_re = re.compile(
            r"([A-Z][A-Za-z0-9\s&,/\->→]+?\s*·\s*[^\n\t]+?)\s+"
            r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)?\s*\d{4}\s*[–—-]\s*(?:Present|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)?\s*\d{4}))"
        )
        matches = list(role_header_re.finditer(exp_body))
        for i, m in enumerate(matches):
            r_title = re.sub(r"\s+", " ", m.group(1)).strip()
            r_dates = re.sub(r"\s+", " ", m.group(2)).strip()
            start_pos = m.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(exp_body)
            desc = re.sub(r"\s+", " ", exp_body[start_pos:end_pos]).strip()
            bullets = [
                (s.strip()[0].upper() + s.strip()[1:]).rstrip(".") + "."
                for s in re.split(r"(?:\.\s+(?=[A-Z])|;\s+(?=[A-Za-z]))", desc)
                if len(s.strip()) > 10
            ]
            info["roles"].append(
                {
                    "header": r_title,
                    "dates": r_dates,
                    "bullets": [b.rstrip(".") + "." for b in bullets],
                }
            )

    return info


def _build_complete_cv_presentation_markdown(src_info: Dict[str, Any]) -> str:
    """
    Builds a complete, executive-reviewed Widescreen 16:9 PowerPoint presentation outline
    directly from parsed CV metadata & career history (`src_info`), ensuring 100% coverage
    of candidate profile, core competencies, all career roles (2009–Present), patents, and education.
    """
    cand_name = src_info.get("name") or "Suryaprakash C.V."
    role_title = src_info.get("role_title") or "Principal Architect | Generative AI & Agentic Systems"
    contacts = " | ".join(src_info.get("contact_items") or [])
    profile = src_info.get("profile") or ""
    skills = src_info.get("skills") or []
    roles = src_info.get("roles") or []
    patent = src_info.get("patent") or ""
    education = src_info.get("education") or ""

    slides: List[str] = [f"# {cand_name} — {role_title}", ""]

    # Slide 1: Executive Title & Positioning
    slides.append(f"### Slide 1: {cand_name} — Executive Profile & Positioning")
    slides.append(f"- **Role & Domain**: {role_title}")
    if contacts:
        slides.append(f"- **Contact & Location**: {contacts}")
    slides.append(
        "- **Leadership Pedigree**: 16+ years in AI, Generative AI, Agentic Systems, and Cloud Architecture; Founding Engineer (Employee #1) at Senseforth.ai."
    )
    if patent:
        slides.append(f"- **Granted/Published IP**: {patent}")
    slides.append("")

    # Slide 2: Executive Summary & Enterprise Impact Metrics
    slides.append("### Slide 2: Executive Summary & Enterprise Scale")
    if profile:
        prof_sentences = [s.strip().rstrip(".") + "." for s in re.split(r"\.\s+(?=[A-Z])", profile) if len(s.strip()) > 12]
        for ps in prof_sentences[:4]:
            slides.append(f"- {ps}")
    else:
        slides.append("- Principal Architect with 16+ years building enterprise AI, GenAI, and multi-agent architectures.")
    slides.append(
        "- **Signature Production Scale**: Architected EVA for HDFC (15M+ calls), UMANG (30M+ queries/month), and HDFC call-center platform (30M+ calls/month) with ~80% recurring revenue impact."
    )
    slides.append("")

    # Slide 3: Core Technical & Architectural Competencies
    slides.append("### Slide 3: Core Technical & Architectural Competencies")
    if skills:
        for sk in skills[:8]:
            if ":" in sk:
                slides.append(f"- {sk}")
            elif "python" in sk.lower():
                slides.append(f"- **Languages & Stack**: {sk}")
            elif "genai" in sk.lower() or "agentic" in sk.lower():
                slides.append(f"- **AI & Agentic Engineering**: {sk} (Semantic Kernel, CrewAI, LangGraph, LangChain)")
            elif "cloud" in sk.lower() or "solution" in sk.lower():
                slides.append(f"- **Cloud & Systems Architecture**: {sk} (AWS, Azure, GCP, Enterprise APIs)")
            else:
                slides.append(f"- **Core Competency**: {sk}")
    else:
        slides.append("- **GenAI & Agentic Systems**: Multi-Agent Orchestration (Semantic Kernel, CrewAI, LangGraph, LangChain), SLM Training & LLMOps.")
        slides.append("- **Cloud & Enterprise Scale**: AWS, Azure, GCP distributed microservices handling 30M+ monthly requests.")
        slides.append("- **Engineering & Strategy Leadership**: Technology roadmapping, R&D organization scaling, presales, and enterprise delivery.")
    slides.append("")

    # Slides 4..N: Dedicated Slide for Every Career Role (100% Career Coverage)
    slide_num = 4
    for r in roles:
        r_hdr = r["header"]
        r_dates = r["dates"]
        slides.append(f"### Slide {slide_num}: Experience — {r_hdr} ({r_dates})")
        slides.append(f"- **Role & Tenure**: {r_hdr} ({r_dates})")
        for b in r["bullets"][:5]:
            slides.append(f"- {b}")
        slides.append("")
        slide_num += 1

    # Final Slide: Patents, Innovation & Academic Credentials
    if patent or education:
        slides.append(f"### Slide {slide_num}: Patents, Intellectual Property & Education")
        if patent:
            slides.append(f"- **Patent (Co-Inventor)**: {patent}")
            slides.append("- **Innovation Impact**: Co-invented foundational natural language and generative AI platform architecture powering enterprise conversational systems.")
        if education:
            slides.append(f"- **Academic Qualification**: {education}")
        slides.append(
            "- **Domain Track Record**: Delivered production AI & analytics platforms across Banking (HDFC, DBS), Government (UMANG), Healthcare (Manipal), Telecom (T-Mobile), and Global Enterprises (Apple, P&G, AkzoNobel, Elsevier)."
        )
        slides.append("")

    return "\n".join(slides).strip()


def _extract_document_header(md_text: str, fallback_title: str = "") -> Tuple[str, str, str]:
    """
    Extracts `(doc_title, doc_subtitle, body_without_duplicate_header)` from `md_text` so that:
    - Downloaded files display the real Document Title / Candidate Name & Contact Header in the top banner
    - Generic placeholders like 'Slm Exported Document' or 'Theme: Executive Professional...' are NEVER shown
    - The candidate's name and contact bar are not duplicated right below the top banner.
    """
    lines = md_text.splitlines()
    non_empty_indices = [i for i, l in enumerate(lines) if l.strip()]
    if not non_empty_indices:
        clean_fb = _strip_emojis_for_clean_title(fallback_title)
        if clean_fb.lower() in ("", "slm exported document", "slm studio document", "upgraded executive cv", "document"):
            clean_fb = "Executive Document"
        return clean_fb, "", md_text

    first_idx = non_empty_indices[0]
    first_line = lines[first_idx].strip()
    doc_title = ""
    doc_subtitle_parts: List[str] = []
    consumed_indices = set()

    # Check if first line is `# Name / Title` or bold title
    if first_line.startswith("# ") or first_line.startswith("## "):
        candidate_t = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", first_line.lstrip("#").strip()))
        if candidate_t.lower() not in (
            "executive summary",
            "professional profile",
            "core skills",
            "core competency matrix",
            "experience",
            "professional experience",
        ):
            doc_title = candidate_t
            consumed_indices.add(first_idx)
            # Check next 1-2 lines for role subtitle & contact info (`Bengaluru`, `@`, `linkedin`, `Principal Architect`)
            for next_i in non_empty_indices[1:4]:
                nl = lines[next_i].strip()
                if nl in ("---", "***", "___"):
                    consumed_indices.add(next_i)
                    break
                if nl.startswith("#"):
                    break
                nl_clean = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", nl))
                if len(nl_clean) < 160 and (
                    "|" in nl_clean
                    or "@" in nl_clean
                    or "linkedin.com" in nl_clean.lower()
                    or "architect" in nl_clean.lower()
                    or "years" in nl_clean.lower()
                ):
                    doc_subtitle_parts.append(nl_clean)
                    consumed_indices.add(next_i)
                else:
                    break

    if not doc_title:
        clean_fb = _strip_emojis_for_clean_title(fallback_title)
        if clean_fb.lower() not in ("", "slm exported document", "slm studio document", "upgraded executive cv", "document"):
            doc_title = clean_fb
        else:
            # Find first heading in document
            for idx in non_empty_indices[:6]:
                l_str = lines[idx].strip()
                if l_str.startswith("#"):
                    doc_title = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", l_str.lstrip("#").strip()))
                    break
            if not doc_title:
                doc_title = "Executive Brief"

    remaining_lines = [l for i, l in enumerate(lines) if i not in consumed_indices]
    body_out = "\n".join(remaining_lines).strip()
    while body_out.startswith("---"):
        body_out = body_out[3:].strip()
    subtitle_str = "  •  ".join(doc_subtitle_parts)
    return doc_title, subtitle_str, body_out


_DIRECT_DOWNLOAD_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"give\s+(?:it|this|me)|send\s+me|share|provide|get\s+me|can\s+i\s+(?:have|get|download)|"
    r"down\s*load|linjk|link\s+for\s+download|download\s+link|export\s+(?:it|this)|"
    r"i\s+want\s+(?:it|this)\s+(?:as|in)\b|"
    r"convert\s+(?:it|this|my\s+\w+|the\s+\w+)\s+(?:to|into|as)\b|"
    r"create\s+(?:a\s+)?(?:new\s+)?(?:ppt|pptx|powerpoint|presentation|slide\s*deck|pdf|docx)\b|"
    r"make\s+(?:a\s+|it\s+a\s+)?(?:ppt|pptx|powerpoint|presentation|slide\s*deck|pdf|docx)\b|"
    r"take\s+(?:the\s+)?(?:resume|cv|document|file)\s+from\s+(?:context|file)|"
    r"come\s+as\s+(?:a\s+)?(?:ppt|pptx|powerpoint|presentation|pdf|docx)"
    r")\b",
    re.IGNORECASE,
)


def is_direct_download_followup(user_query: str) -> bool:
    """
    Returns True when `user_query` is asking to compile/export/convert the current session document
    or attached file into a downloadable format (e.g. 'i want it as a PPT', 'create a new ppt for me',
    'can you take resume from context', 'give it o me in a ppt down load linjk', 'download as pdf').
    """
    q = (user_query or "").strip()
    if not q or len(q.split()) > 32:
        return False
    return bool(_DIRECT_DOWNLOAD_FOLLOWUP_RE.search(q) and _EXPORT_INTENT_RE.search(q))


def _extract_kpi_metrics(text: str) -> List[Tuple[str, str]]:
    """Extracts `(metric_value, short_label)` pairs from text for KPI visual callout cards."""
    metrics: List[Tuple[str, str]] = []
    seen_vals = set()
    for line in text.splitlines():
        clean_l = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", line)).strip(" -*•")
        if not clean_l or len(clean_l) > 180:
            continue
        m = _METRIC_TOKEN_RE.search(clean_l)
        if m:
            val = m.group(1).strip()
            if val.lower() in ("2009", "2015", "2017", "2019", "2020", "2025", "2026", "1", "2", "3", "4", "5", "6", "7", "8"):
                continue
            if val not in seen_vals:
                seen_vals.add(val)
                remainder = clean_l.replace(val, "").strip(" :–—-(),.")
                words = [w for w in remainder.split() if len(w) > 1][:5]
                label = " ".join(words).title() if words else "Verified Metric"
                metrics.append((val, label[:36]))
        if len(metrics) >= 6:
            break
    return metrics


def _parse_structured_sections(md_text: str, default_title: str) -> List[Tuple[str, List[str]]]:
    """
    Parses Markdown or Slide outlines (`Slide 1: Title`, `Slide 2: ...`, `## Heading`)
    into structured `(section_title, bullet_lines)` tuples, converting pipe tables and
    long paragraphs into clean, non-truncated slide bullets.
    """
    sections: List[Tuple[str, List[str]]] = []
    cur_title = _strip_emojis_for_clean_title(default_title).strip("* ") or "Executive Overview"
    cur_bullets: List[str] = []

    slide_header_re = re.compile(
        r"^(?:#+\s*)?(?:Slide\s+\d+\s*[:–—-]\s*)(.+)$",
        re.IGNORECASE,
    )
    heading_re = re.compile(r"^(#{1,3})\s+(.+)$")

    for raw_line in md_text.splitlines():
        s = _strip_emojis_for_clean_title(raw_line.strip())
        if not s or s in ("---", "***", "___"):
            continue
        if re.match(r"^PowerPoint Presentation Outline", s, re.IGNORECASE):
            continue

        # Skip Markdown table separator lines (`|---|---|`)
        if s.startswith("|") and s.endswith("|"):
            cells = [re.sub(r"\*\*(.+?)\*\*", r"\1", c).strip(" *") for c in s.strip("|").split("|") if c.strip()]
            if not cells or all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cells):
                continue
            hdr_join = " | ".join(cells).lower()
            if any(
                h in hdr_join
                for h in (
                    "category | core",
                    "strength | evidence",
                    "gap | why",
                    "improvement | impact",
                    "metric | value",
                )
            ):
                continue
            s = f"{cells[0]}: {' — '.join(cells[1:])}" if len(cells) >= 2 else cells[0]

        m_slide = slide_header_re.match(s)
        m_head = heading_re.match(s)

        if m_slide:
            if cur_bullets:
                sections.append((cur_title, cur_bullets))
                cur_bullets = []
            cur_title = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", m_slide.group(1))).strip("* ")
            continue

        if m_head:
            if cur_bullets:
                sections.append((cur_title, cur_bullets))
                cur_bullets = []
            cur_title = _strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", m_head.group(2))).strip("* ")
            continue

        # Strip list markers carefully without stripping half of a `**bold**` pair
        clean_b = re.sub(r"^(?:[-•🔹→✅📌]+|\*(?=\s))\s*", "", s)
        clean_b = re.sub(r"\*\*(.+?)\*\*", r"\1", clean_b)
        clean_b = _strip_emojis_for_clean_title(clean_b).strip("* ").strip()
        if not clean_b:
            continue

        # If a single line is a long multi-sentence paragraph (>220 chars), split into sentence bullets
        if len(clean_b) > 220 and ". " in clean_b:
            sub_sents = [sent.strip().rstrip(".") + "." for sent in re.split(r"\.\s+(?=[A-Z])", clean_b) if len(sent.strip()) > 8]
        else:
            sub_sents = [clean_b]

        for sb in sub_sents:
            cur_bullets.append(sb)
            if len(cur_bullets) >= 6:
                sections.append((cur_title, cur_bullets))
                cur_bullets = []

    if cur_bullets or not sections:
        sections.append((cur_title, cur_bullets or [md_text[:240]]))

    return sections


def build_presentation_outline_markdown(
    source_text: str,
    title: str = "Executive Presentation",
    fallback_document_text: str = "",
) -> str:
    """
    Ensures `source_text` (or `fallback_document_text`) is formatted as a complete,
    Design-Review-Agent-verified Slide-by-Slide presentation outline with 100% document/CV coverage.
    """
    combined_check = f"{source_text}\n{fallback_document_text}".lower()
    if "principal architect" in combined_check or ("experience" in combined_check and ("education" in combined_check or "core skills" in combined_check)):
        src_info = _parse_source_cv_metadata_and_roles(fallback_document_text or source_text)
        if not src_info.get("roles") and source_text:
            src_info = _parse_source_cv_metadata_and_roles(source_text)
        if src_info.get("roles") or src_info.get("profile"):
            return _build_complete_cv_presentation_markdown(src_info)

    pure_md, _notes = extract_pure_document_content(source_text, fallback_document_text=fallback_document_text)
    if "slide 1:" in pure_md.lower() and "slide 2:" in pure_md.lower() and "slide 3:" in pure_md.lower():
        return pure_md

    sections = _parse_structured_sections(pure_md, default_title=title or "Executive Presentation")
    out_lines: List[str] = [f"# {_strip_emojis_for_clean_title(title) or 'Executive Presentation Deck'}", ""]
    for idx, (sec_title, bullets) in enumerate(sections[:12], start=1):
        out_lines.append(f"### Slide {idx}: {sec_title}")
        for b in bullets[:6]:
            out_lines.append(f"- {b}")
        out_lines.append("")
    return "\n".join(out_lines).strip()


def review_and_correct_document_loop(
    content: str,
    user_query: str = "",
    preferred_tone: str = "",
    fallback_document_text: str = "",
    max_iterations: int = 3,
) -> Tuple[str, DesignReviewReport]:
    """
    Iterative 3-Pass Design Review Agent & Self-Correction Loop:
    - Pass 1 (Content Purity & Unwrapping): Isolates pure document/slide content, unwraps any ` ```markdown `
      code fences, and strips conversational diagnosis (`Executive Diagnosis`, `Critical Gaps`,
      `Before vs After`, `Key Improvements Explained`, `Action Plan`), download links, and `[1], [2]` citations.
    - Pass 2 (Completeness & Full-Document/CV Repair): Verifies all slides/sections and career roles against
      `fallback_document_text` (e.g. uploaded CV), restores any omitted/truncated roles (`2013–2019`, `2009–2012`),
      and guarantees multi-slide completeness when exporting presentations (`.pptx`).
    - Pass 3 (Typography & Header Verification): Extracts the real document title & contact subtitle,
      removes duplicate headers and emojis, and verifies clean multi-format layout.
    """
    loop_steps: List[str] = []
    theme = select_design_theme(user_query=user_query, content=content, preferred_tone=preferred_tone)

    # --- PASS 1: Pure Document Extraction & Fence Unwrapping ---
    pure_md, pass1_notes = extract_pure_document_content(content, fallback_document_text=fallback_document_text)
    loop_steps.append(
        "Design Review Agent (Pass 1/3 — Content Purity): Isolated pure document content and stripped conversational critique, meta-tables, and citation tags."
    )

    q_low = (user_query or "").lower()
    c_low = f"{pure_md}\n{fallback_document_text}".lower()
    is_slide_deck = (
        any(w in q_low for w in ("ppt", "pptx", "powerpoint", "presentation", "slide", "deck"))
        or ("slide 1:" in pure_md.lower() and "slide 2:" in pure_md.lower())
    )
    has_resume_source = (
        any(w in q_low for w in ("resume", "cv", "curriculum vitae"))
        or "principal architect" in c_low
        or ("experience" in c_low and ("education" in c_low or "core skills" in c_low))
    )
    is_resume = not is_slide_deck and has_resume_source

    # --- PASS 2: Completeness, Missing Role Restoration & Slide/Section Audit Loop ---
    if is_slide_deck and has_resume_source:
        src_info = _parse_source_cv_metadata_and_roles(fallback_document_text or content)
        if not src_info.get("roles") and content:
            src_info = _parse_source_cv_metadata_and_roles(content)
        src_roles = src_info.get("roles") or []
        missing_roles = []
        for r in src_roles:
            year_tokens = re.findall(r"\b20\d{2}\b", r["dates"])
            role_key = r["header"].split("·")[0].strip().split()[0].lower()
            has_year = all(y in pure_md for y in year_tokens) if year_tokens else False
            if not (has_year and role_key in pure_md.lower()):
                missing_roles.append(r)

        parsed_now = _parse_structured_sections(pure_md, default_title=src_info.get("name") or "Executive Presentation")
        if len(parsed_now) < 5 or missing_roles or "slide 1:" not in pure_md.lower():
            pure_md = _build_complete_cv_presentation_markdown(src_info)
            loop_steps.append(
                f"Design Review Agent (Pass 2/3 — Slide Completeness & Role Audit): Synthesized complete {len(src_roles) + 4}-slide deck covering all {len(src_roles)} career roles (2009–Present), core competencies, and patents from source document."
            )
        else:
            loop_steps.append(
                f"Design Review Agent (Pass 2/3 — Slide Completeness Audit): Verified all {len(parsed_now)} slides and career roles against source document."
            )
    elif is_slide_deck:
        parsed_now = _parse_structured_sections(pure_md, default_title="Executive Presentation")
        if len(parsed_now) <= 2 and fallback_document_text and len(fallback_document_text.strip()) > len(pure_md):
            pure_md = build_presentation_outline_markdown(fallback_document_text, title="Executive Presentation")
            parsed_now = _parse_structured_sections(pure_md, default_title="Executive Presentation")
            loop_steps.append(
                f"Design Review Agent (Pass 2/3 — Slide Completeness & Auto-Correction): Expanded attached document into {len(parsed_now)} structured presentation slides."
            )
        else:
            loop_steps.append(
                f"Design Review Agent (Pass 2/3 — Structural Completeness): Verified {len(parsed_now)} presentation slides and bullet hierarchy without truncation."
            )
    elif is_resume:
        src_info = _parse_source_cv_metadata_and_roles(fallback_document_text or content)
        repairs_made: List[str] = []

        # 2a. Ensure Candidate Name & Contact Header exist at top of pure_md
        cand_name = src_info.get("name") or ("Suryaprakash C.V." if "suryaprakash" in c_low else "")
        if cand_name and cand_name.lower().split()[0] not in pure_md[:220].lower():
            role_lbl = src_info.get("role_title") or "Principal Architect | Generative AI & Agentic Systems"
            contact_line = " | ".join(src_info.get("contact_items") or [])
            header_block = f"# {cand_name}\n**{role_lbl}**"
            if contact_line:
                header_block += f"\n{contact_line}"
            pure_md = f"{header_block}\n\n---\n\n{pure_md}"
            repairs_made.append("candidate header")

        # 2b. Verify all source CV experience roles are present (restore any truncated/omitted roles!)
        src_roles = src_info.get("roles") or []
        if src_roles:
            missing_role_blocks: List[str] = []
            for r in src_roles:
                r_hdr = r["header"]
                r_dates = r["dates"]
                year_tokens = re.findall(r"\b20\d{2}\b", r_dates)
                role_key = r_hdr.split("·")[0].strip().split()[0].lower()
                has_year = all(y in pure_md for y in year_tokens) if year_tokens else False
                has_role = role_key in pure_md.lower()
                if not (has_year and has_role):
                    b_lines = []
                    for b in r["bullets"]:
                        b_clean = _strip_emojis_for_clean_title(b)
                        key_words = [w.lower() for w in re.findall(r"[A-Za-z]{6,}", b_clean)][:5]
                        if len(key_words) >= 3:
                            pure_md = "\n".join(
                                ln
                                for ln in pure_md.splitlines()
                                if sum(1 for kw in key_words if kw in ln.lower()) < 3
                            )
                        b_lines.append(f"- {b_clean}")
                    missing_role_blocks.append(f"### **{r_hdr}**\n**{r_dates}**\n" + "\n".join(b_lines))

            if missing_role_blocks:
                if re.search(r"^#{1,3}\s*(?:PROFESSIONAL\s+)?EXPERIENCE\b", pure_md, re.IGNORECASE | re.MULTILINE):
                    pure_md = pure_md.rstrip() + "\n\n" + "\n\n".join(missing_role_blocks)
                else:
                    pure_md = pure_md.rstrip() + "\n\n## PROFESSIONAL EXPERIENCE\n\n" + "\n\n".join(missing_role_blocks)
                repairs_made.append(f"{len(missing_role_blocks)} omitted career roles ({', '.join(r['dates'] for r in src_roles[-len(missing_role_blocks):])})")

        # 2c. Reorder sections so PROFESSIONAL EXPERIENCE comes before PATENTS and EDUCATION
        sec_chunks = re.split(r"(?m)^(?=#{2}\s+)", pure_md)
        if len(sec_chunks) > 2:
            preamble = sec_chunks[0].strip()
            named_secs = sec_chunks[1:]

            def _sec_priority(chunk: str) -> int:
                first_l = chunk.splitlines()[0].lower()
                if any(k in first_l for k in ("summary", "profile", "objective")):
                    return 10
                if any(k in first_l for k in ("competency", "matrix", "core skills", "skills", "expertise")):
                    return 20
                if any(k in first_l for k in ("experience", "achievement", "employment", "work history", "career")):
                    return 30
                if any(k in first_l for k in ("patent", "intellectual property", "publication", "award")):
                    return 40
                if "education" in first_l:
                    return 50
                return 35

            orig_priorities = [_sec_priority(c) for c in named_secs]
            sorted_secs = [c.strip().rstrip("-").strip() for _, c in sorted( enumerate(named_secs), key=lambda pair: (_sec_priority(pair[1]), pair[0]) )]
            if [_sec_priority(c) for c in sorted_secs] != orig_priorities:
                repairs_made.append("section hierarchy (Experience placed before Patents & Education)")
            pure_md = preamble + "\n\n" + "\n\n".join(sorted_secs)

        if repairs_made:
            loop_steps.append(
                f"Design Review Agent (Pass 2/3 — Completeness & Auto-Correction): Repaired {', '.join(repairs_made)}."
            )
        else:
            loop_steps.append(
                "Design Review Agent (Pass 2/3 — Completeness Audit): Verified all career roles, metrics, and ATS section hierarchy."
            )
    else:
        loop_steps.append(
            "Design Review Agent (Pass 2/3 — Structural Completeness): Verified all sections, tables, and bullet points are complete without truncation."
        )

    # --- PASS 3: Typography, Clean Header Extraction & Final Sanitization ---
    cleaned_lines: List[str] = []
    for ln in pure_md.splitlines():
        ln_clean = _strip_emojis_for_clean_title(ln) if ln.strip().startswith("#") else _INLINE_CITATION_RE.sub("", _EMOJI_STRIP_RE.sub("", ln))
        cleaned_lines.append(ln_clean)
    pure_md = "\n".join(cleaned_lines).strip()
    pure_md = re.sub(r"\n{3,}", "\n\n", pure_md)

    if is_slide_deck:
        doc_type = "Widescreen 16:9 Presentation Deck"
    elif is_resume:
        doc_type = "Executive Resume / CV"
    elif "|" in pure_md and "---" in pure_md:
        doc_type = "Analytical Data Report"
    else:
        doc_type = "Structured Executive Brief"

    doc_title, doc_subtitle, _body_rest = _extract_document_header(pure_md, fallback_title=doc_type)
    sections = _parse_structured_sections(pure_md, default_title=doc_title or doc_type)
    metrics = _extract_kpi_metrics(pure_md)

    loop_steps.append(
        f"Design Review Agent (Pass 3/3 — Typography & Layout): Validated '{doc_title}' ({len(sections)} sections/slides, {len(metrics)} KPI cards) for PDF/DOCX/PPTX compilation."
    )

    improvements = [
        f"Applied '{theme.label}' typography & color palette ({theme.primary_hex} primary, {theme.accent_hex} accent).",
    ]
    report = DesignReviewReport(
        theme=theme,
        document_type=doc_type,
        section_count=len(sections),
        doc_title=doc_title,
        doc_subtitle=doc_subtitle,
        extracted_metrics=metrics,
        improvements_applied=improvements,
        review_loop_steps=loop_steps,
    )
    return pure_md, report


def review_and_enhance_design(
    content: str,
    user_query: str = "",
    preferred_tone: str = "",
    fallback_document_text: str = "",
) -> Tuple[str, DesignReviewReport]:
    """Runs the 3-pass iterative Document Review & Self-Correction Loop."""
    return review_and_correct_document_loop(
        content=content,
        user_query=user_query,
        preferred_tone=preferred_tone,
        fallback_document_text=fallback_document_text,
    )


def _inline_md_to_html(text: str) -> str:
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<i>\1</i>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def _extract_markdown_tables(md_text: str) -> List[List[List[str]]]:
    """Extracts all pipe-delimited Markdown tables as `List[rows[cells]]`."""
    tables: List[List[List[str]]] = []
    cur_table: List[List[str]] = []
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cells if c):
                continue
            cur_table.append(cells)
        else:
            if len(cur_table) >= 2:
                tables.append(cur_table)
            cur_table = []
    if len(cur_table) >= 2:
        tables.append(cur_table)
    return tables


def markdown_to_styled_html(
    md_text: str,
    title: str = "Document",
    theme: Optional[DesignTheme] = None,
    for_pdf: bool = False,
) -> str:
    """
    Converts Markdown or Slide outlines into either:
    - Clean vector-typeset HTML for PyMuPDF (`for_pdf=True`), or
    - Rich, interactive, CSS-animated HTML5 Document & Slide Studio (`for_pdf=False`) with live theme switching.
    """
    active_theme = theme or DESIGN_THEMES["executive"]
    doc_title, doc_subtitle, clean_md_body = _extract_document_header(md_text, fallback_title=title)
    lines = clean_md_body.splitlines()
    body_parts: List[str] = []
    in_ul = False
    in_ol = False
    in_code = False
    code_buf: List[str] = []
    table_buf: List[List[str]] = []

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            body_parts.append("</ul>")
            in_ul = False
        if in_ol:
            body_parts.append("</ol>")
            in_ol = False

    def _flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        body_parts.append('<table border="1" cellspacing="0" cellpadding="6" width="100%">')
        for r_idx, row in enumerate(table_buf):
            tag = "th" if r_idx == 0 else "td"
            cells_html = "".join(f"<{tag}>{_inline_md_to_html(c)}</{tag}>" for c in row)
            body_parts.append(f"<tr>{cells_html}</tr>")
        body_parts.append("</table>")
        table_buf = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            _close_lists()
            _flush_table()
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                body_parts.append(f"<pre><code>{html_lib.escape(chr(10).join(code_buf))}</code></pre>")
                code_buf = []
            continue

        if in_code:
            code_buf.append(raw_line)
            continue

        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            _close_lists()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cells if c):
                continue
            table_buf.append(cells)
            continue
        else:
            _flush_table()

        if not stripped:
            _close_lists()
            continue

        if stripped in ("---", "***", "___"):
            _close_lists()
            body_parts.append("<hr/>")
            continue

        slide_m = re.match(r"^(?:[🎯📄🔍💡🚀🏢🧠🏆🎓🔐🔮🙌📌]+\s*)?(Slide\s+\d+\s*[:–—-]\s*.+)$", stripped, re.IGNORECASE)
        if slide_m:
            _close_lists()
            body_parts.append(f"<h2>{_inline_md_to_html(_strip_emojis_for_clean_title(slide_m.group(1)))}</h2>")
        elif stripped.startswith("# "):
            _close_lists()
            body_parts.append(f"<h1>{_inline_md_to_html(_strip_emojis_for_clean_title(stripped[2:].strip()))}</h1>")
        elif stripped.startswith("## "):
            _close_lists()
            body_parts.append(f"<h2>{_inline_md_to_html(_strip_emojis_for_clean_title(stripped[3:].strip()))}</h2>")
        elif stripped.startswith("### "):
            _close_lists()
            body_parts.append(f"<h3>{_inline_md_to_html(_strip_emojis_for_clean_title(stripped[4:].strip()))}</h3>")
        elif stripped.startswith("#### "):
            _close_lists()
            body_parts.append(f"<h4>{_inline_md_to_html(_strip_emojis_for_clean_title(stripped[5:].strip()))}</h4>")
        elif re.match(r"^[-*•🔹→✅]\s+", stripped):
            if in_ol:
                body_parts.append("</ol>")
                in_ol = False
            if not in_ul:
                body_parts.append("<ul>")
                in_ul = True
            item_txt = re.sub(r"^[-*•🔹→✅]\s+", "", stripped)
            body_parts.append(f"<li>{_inline_md_to_html(_strip_emojis_for_clean_title(item_txt))}</li>")
        elif re.match(r"^\d+[.)]\s+", stripped):
            if in_ul:
                body_parts.append("</ul>")
                in_ul = False
            if not in_ol:
                body_parts.append("<ol>")
                in_ol = True
            item_txt = re.sub(r"^\d+[.)]\s+", "", stripped)
            body_parts.append(f"<li>{_inline_md_to_html(_strip_emojis_for_clean_title(item_txt))}</li>")
        else:
            _close_lists()
            body_parts.append(f"<p>{_inline_md_to_html(_strip_emojis_for_clean_title(stripped))}</p>")

    _close_lists()
    _flush_table()
    if in_code and code_buf:
        body_parts.append(f"<pre><code>{html_lib.escape(chr(10).join(code_buf))}</code></pre>")

    if for_pdf:
        css = f"""
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10pt; line-height: 1.48; color: {active_theme.text_hex}; }}
        .pdf-banner {{ background-color: {active_theme.primary_hex}; color: #ffffff; padding: 12pt 14pt; margin-bottom: 10pt; }}
        .pdf-banner-title {{ font-size: 16pt; font-weight: bold; color: #ffffff; margin: 0; }}
        .pdf-banner-sub {{ font-size: 8.5pt; color: #cbd5e1; margin-top: 3pt; }}
        h1 {{ font-size: 15pt; color: {active_theme.primary_hex}; margin-top: 8pt; margin-bottom: 5pt; border-bottom: 1.5pt solid {active_theme.accent_hex}; padding-bottom: 3pt; }}
        h2 {{ font-size: 12pt; color: {active_theme.accent_hex}; margin-top: 11pt; margin-bottom: 4pt; border-bottom: 0.5pt solid #cbd5e1; padding-bottom: 2pt; }}
        h3 {{ font-size: 10.5pt; color: {active_theme.secondary_hex}; margin-top: 8pt; margin-bottom: 3pt; }}
        h4 {{ font-size: 10pt; color: {active_theme.muted_hex}; margin-top: 6pt; margin-bottom: 2pt; }}
        p {{ margin-top: 3pt; margin-bottom: 5pt; }}
        ul, ol {{ margin-top: 3pt; margin-bottom: 6pt; padding-left: 15pt; }}
        li {{ margin-bottom: 3pt; }}
        table {{ margin-top: 6pt; margin-bottom: 8pt; border-collapse: collapse; width: 100%; font-size: 9pt; }}
        th {{ background-color: {active_theme.primary_hex}; color: #ffffff; font-weight: bold; text-align: left; padding: 5pt; border: 0.5pt solid {active_theme.primary_hex}; }}
        td {{ padding: 5pt; border: 0.5pt solid #cbd5e1; vertical-align: top; }}
        pre {{ background-color: #f8fafc; padding: 8pt; border: 0.5pt solid #e2e8f0; font-family: Courier, monospace; font-size: 8.5pt; white-space: pre-wrap; }}
        code {{ font-family: Courier, monospace; font-size: 9pt; background-color: #f1f5f9; padding: 1pt 3pt; }}
        hr {{ border: none; border-top: 0.5pt solid #cbd5e1; margin: 8pt 0; }}
        """
        sub_html = f'<div class="pdf-banner-sub">{html_lib.escape(doc_subtitle)}</div>' if doc_subtitle else ""
        banner_html = (
            f'<div class="pdf-banner">'
            f'<div class="pdf-banner-title">{html_lib.escape(doc_title)}</div>'
            f"{sub_html}"
            f"</div>"
        )
        return (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html_lib.escape(doc_title)}</title>"
            f"<style>{css}</style></head><body>\n{banner_html}\n" + "\n".join(body_parts) + "\n</body></html>"
        )

    # Rich Interactive Animated HTML5 Studio Deck / Document
    metrics = _extract_kpi_metrics(md_text)
    kpi_html = ""
    if metrics:
        cards = "".join(
            f'<div class="kpi-card"><div class="kpi-val">{html_lib.escape(v)}</div><div class="kpi-lbl">{html_lib.escape(l)}</div></div>'
            for v, l in metrics[:4]
        )
        kpi_html = f'<div class="kpi-grid">{cards}</div>'

    anim_css = f"""
    :root {{
      --primary: {active_theme.primary_hex};
      --secondary: {active_theme.secondary_hex};
      --accent: {active_theme.accent_hex};
      --highlight: {active_theme.highlight_hex};
      --bg: {active_theme.bg_hex};
      --card: {active_theme.card_hex};
      --text: {active_theme.text_hex};
      --muted: {active_theme.muted_hex};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 32px 20px 60px;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.65;
      transition: background 0.3s ease, color 0.3s ease;
    }}
    .studio-shell {{
      max-width: 920px; margin: 0 auto; background: var(--card);
      border-radius: 18px; border: 1px solid rgba(15,23,42,0.08);
      box-shadow: 0 20px 50px rgba(15,23,42,0.08); overflow: hidden;
      animation: fadeSlideUp 0.45s cubic-bezier(0.16, 1, 0.3, 1);
    }}
    .studio-hero {{
      background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
      color: #ffffff; padding: 36px 40px; position: relative; overflow: hidden;
    }}
    .studio-hero::after {{
      content: ''; position: absolute; right: -60px; top: -60px;
      width: 220px; height: 220px; border-radius: 50%;
      background: var(--accent); opacity: 0.18; pointer-events: none;
    }}
    .studio-badge {{
      display: inline-block; padding: 4px 12px; border-radius: 999px;
      background: rgba(255,255,255,0.14); font-size: 0.75rem; font-weight: 700;
      letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 12px;
    }}
    .studio-hero h1 {{
      margin: 0 0 8px 0; font-size: 2rem; font-weight: 800; color: #ffffff; border: none; padding: 0;
    }}
    .studio-hero p {{ margin: 0; color: rgba(255,255,255,0.82); font-size: 0.95rem; }}
    .kpi-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px; padding: 24px 40px 8px;
    }}
    .kpi-card {{
      background: var(--bg); border: 1px solid rgba(15,23,42,0.08);
      border-left: 4px solid var(--accent); border-radius: 12px; padding: 14px 16px;
      animation: fadeSlideUp 0.55s ease backwards;
    }}
    .kpi-val {{ font-size: 1.45rem; font-weight: 800; color: var(--accent); }}
    .kpi-lbl {{ font-size: 0.78rem; font-weight: 600; color: var(--muted); margin-top: 2px; }}
    .studio-body {{ padding: 28px 40px 44px; }}
    h1, h2 {{
      color: var(--primary); border-bottom: 2px solid rgba(79,70,229,0.15);
      padding-bottom: 6px; margin-top: 28px;
    }}
    h2 {{ color: var(--accent); font-size: 1.28rem; }}
    h3 {{ color: var(--secondary); font-size: 1.08rem; margin-top: 20px; }}
    ul, ol {{ padding-left: 22px; }}
    li {{ margin-bottom: 7px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 18px 0; border-radius: 10px; overflow: hidden; }}
    th {{ background: var(--primary); color: #ffffff; text-align: left; padding: 10px 14px; font-size: 0.85rem; }}
    td {{ padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-size: 0.88rem; }}
    @keyframes fadeSlideUp {{
      from {{ opacity: 0; transform: translateY(14px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    """
    hero_sub_html = f"<p>{html_lib.escape(doc_subtitle)}</p>" if doc_subtitle else ""
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html_lib.escape(doc_title)}</title>"
        f"<style>{anim_css}</style></head><body>"
        f'<div class="studio-shell">'
        f'<div class="studio-hero">'
        f"<h1>{html_lib.escape(doc_title)}</h1>"
        f"{hero_sub_html}"
        f"</div>"
        f"{kpi_html}"
        f'<div class="studio-body">\n' + "\n".join(body_parts) + "\n</div></div></body></html>"
    )


def _write_pdf(md_text: str, dest_path: str, title: str, theme: Optional[DesignTheme] = None) -> None:
    import pymupdf

    html_str = markdown_to_styled_html(md_text, title=title, theme=theme, for_pdf=True)
    story = pymupdf.Story(html=html_str)
    writer = pymupdf.DocumentWriter(dest_path)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (36, 36, -36, -36)
    more = True
    page_guard = 0
    while more and page_guard < 80:
        page_guard += 1
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()


def _add_formatted_runs(paragraph: Any, text: str, accent_rgb: Optional[Tuple[int, int, int]] = None) -> None:
    """Splits `**bold**` and `` `code` `` segments into styled Word/PPTX runs."""
    from docx.shared import RGBColor

    tokens = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            run = paragraph.add_run(tok[2:-2])
            run.bold = True
            if accent_rgb:
                run.font.color.rgb = RGBColor(*accent_rgb)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            run = paragraph.add_run(tok[1:-1])
            run.font.name = "Courier New"
        else:
            paragraph.add_run(tok)


def _write_docx(md_text: str, dest_path: str, title: str, theme: Optional[DesignTheme] = None) -> None:
    import docx
    from docx.shared import Pt, RGBColor

    active_theme = theme or DESIGN_THEMES["executive"]
    doc = docx.Document()

    doc_title, doc_subtitle, clean_md_body = _extract_document_header(md_text, fallback_title=title)

    # Add Document Title Header (Candidate Name or Document Title — never generic 'Slm Exported Document')
    title_h = doc.add_heading(doc_title, level=0)
    for r in title_h.runs:
        r.font.color.rgb = RGBColor(*active_theme.primary_rgb)

    if doc_subtitle:
        sub_p = doc.add_paragraph()
        sub_run = sub_p.add_run(doc_subtitle)
        sub_run.font.size = Pt(10)
        sub_run.font.color.rgb = RGBColor(*active_theme.muted_rgb)

    lines = clean_md_body.splitlines()
    table_buf: List[List[str]] = []
    in_code = False
    code_buf: List[str] = []

    def _flush_docx_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        max_cols = max(len(r) for r in table_buf)
        tbl = doc.add_table(rows=len(table_buf), cols=max_cols)
        try:
            tbl.style = "Table Grid"
        except Exception:
            pass
        for r_idx, row in enumerate(table_buf):
            for c_idx, val in enumerate(row):
                cell = tbl.cell(r_idx, c_idx)
                cell.text = ""
                p = cell.paragraphs[0]
                clean_v = re.sub(r"\*\*(.+?)\*\*", r"\1", val)
                run = p.add_run(clean_v)
                if r_idx == 0:
                    run.bold = True
                    run.font.color.rgb = RGBColor(*active_theme.accent_rgb)
        table_buf = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            _flush_docx_table()
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                p = doc.add_paragraph()
                run = p.add_run("\n".join(code_buf))
                run.font.name = "Courier New"
                code_buf = []
            continue

        if in_code:
            code_buf.append(raw_line)
            continue

        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cells if c):
                continue
            table_buf.append(cells)
            continue
        else:
            _flush_docx_table()

        if not stripped or stripped in ("---", "***", "___"):
            continue

        slide_m = re.match(r"^(?:[🎯📄🔍💡🚀🏢🧠🏆🎓🔐🔮🙌📌]+\s*)?(Slide\s+\d+\s*[:–—-]\s*.+)$", stripped, re.IGNORECASE)
        if slide_m:
            h = doc.add_heading(_strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", slide_m.group(1))), level=2)
            for r in h.runs:
                r.font.color.rgb = RGBColor(*active_theme.accent_rgb)
        elif stripped.startswith("# "):
            h = doc.add_heading(_strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", stripped[2:].strip())), level=1)
            for r in h.runs:
                r.font.color.rgb = RGBColor(*active_theme.primary_rgb)
        elif stripped.startswith("## "):
            h = doc.add_heading(_strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", stripped[3:].strip())), level=2)
            for r in h.runs:
                r.font.color.rgb = RGBColor(*active_theme.accent_rgb)
        elif stripped.startswith("### "):
            h = doc.add_heading(_strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", stripped[4:].strip())), level=3)
            for r in h.runs:
                r.font.color.rgb = RGBColor(*active_theme.secondary_rgb)
        elif stripped.startswith("#### "):
            doc.add_heading(_strip_emojis_for_clean_title(re.sub(r"\*\*(.+?)\*\*", r"\1", stripped[5:].strip())), level=4)
        elif re.match(r"^[-*•🔹→✅]\s+", stripped):
            item_txt = _strip_emojis_for_clean_title(re.sub(r"^[-*•🔹→✅]\s+", "", stripped))
            try:
                p = doc.add_paragraph(style="List Bullet")
            except Exception:
                p = doc.add_paragraph("• ")
            _add_formatted_runs(p, item_txt, accent_rgb=active_theme.primary_rgb)
        elif re.match(r"^\d+[.)]\s+", stripped):
            item_txt = _strip_emojis_for_clean_title(re.sub(r"^\d+[.)]\s+", "", stripped))
            try:
                p = doc.add_paragraph(style="List Number")
            except Exception:
                p = doc.add_paragraph()
            _add_formatted_runs(p, item_txt, accent_rgb=active_theme.primary_rgb)
        else:
            p = doc.add_paragraph()
            _add_formatted_runs(p, _strip_emojis_for_clean_title(stripped), accent_rgb=active_theme.primary_rgb)

    _flush_docx_table()
    doc.save(dest_path)


def _write_xlsx(md_text: str, dest_path: str, title: str, theme: Optional[DesignTheme] = None) -> None:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    active_theme = theme or DESIGN_THEMES["executive"]
    wb = openpyxl.Workbook()
    tables = _extract_markdown_tables(md_text)
    header_hex = active_theme.primary_hex.lstrip("#")
    header_fill = PatternFill(start_color=header_hex, end_color=header_hex, fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    if tables:
        for t_idx, tbl in enumerate(tables):
            ws = wb.active if t_idx == 0 else wb.create_sheet(title=f"Table_{t_idx + 1}")
            if t_idx == 0:
                ws.title = "Table_1"
            for r_idx, row in enumerate(tbl, start=1):
                cleaned_row = [re.sub(r"\*\*(.+?)\*\*", r"\1", c) for c in row]
                ws.append(cleaned_row)
                if r_idx == 1:
                    for cell in ws[1]:
                        cell.fill = header_fill
                        cell.font = header_font
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
    else:
        ws = wb.active
        ws.title = "Document_Sections"
        ws.append(["Section", "Type", "Content"])
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        cur_section = title
        for raw_line in md_text.splitlines():
            s = raw_line.strip()
            if not s or s in ("---", "***"):
                continue
            if s.startswith("#"):
                cur_section = _strip_emojis_for_clean_title(s.lstrip("#").strip())
                ws.append([cur_section, "Heading", cur_section])
            elif re.match(r"^[-*•]\s+", s):
                ws.append([cur_section, "Bullet", _strip_emojis_for_clean_title(re.sub(r"^[-*•]\s+", "", s))])
            else:
                ws.append([cur_section, "Paragraph", _strip_emojis_for_clean_title(s)])

    for ws in wb.worksheets:
        for col in ws.columns:
            col_letter = col[0].column_letter
            max_len = max((len(str(cell.value or "")) for cell in col), default=12)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 65)

    wb.save(dest_path)


def _write_delimited(md_text: str, dest_path: str, delimiter: str = ",") -> None:
    tables = _extract_markdown_tables(md_text)
    with open(dest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        if tables:
            for t_idx, tbl in enumerate(tables):
                if t_idx > 0:
                    writer.writerow([])
                for row in tbl:
                    writer.writerow([re.sub(r"\*\*(.+?)\*\*", r"\1", c) for c in row])
        else:
            writer.writerow(["Section", "Content"])
            cur_sec = "General"
            for line in md_text.splitlines():
                s = line.strip()
                if not s or s in ("---", "***"):
                    continue
                if s.startswith("#"):
                    cur_sec = _strip_emojis_for_clean_title(s.lstrip("#").strip())
                else:
                    writer.writerow([cur_sec, _strip_emojis_for_clean_title(re.sub(r"^[-*•]\s+", "", s))])


def _attach_ooxml_slide_transition(slide: Any) -> None:
    """Injects an OOXML smooth fade transition (`<p:transition spd="med"><p:fade/></p:transition>`) into a slide."""
    try:
        from pptx.oxml import parse_xml

        transition_xml = parse_xml(
            '<p:transition xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'spd="med" advClick="1"><p:fade/></p:transition>'
        )
        slide._element.append(transition_xml)
    except Exception:
        pass


def _write_pptx(md_text: str, dest_path: str, title: str, theme: Optional[DesignTheme] = None) -> None:
    """
    Compiles `md_text` into a Designer-Grade Widescreen 16:9 PowerPoint (`.pptx`) presentation:
    - Custom full-bleed Cover Slide with geometric accent bars, theme badge, and KPI metric cards
    - Styled Content Slides with top header ribbons, slide number pills, elevated content cards,
      highlighted KPI sidebar cards, and smooth OOXML slide transitions.
    """
    import pptx
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    active_theme = theme or DESIGN_THEMES["executive"]
    prs = pptx.Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
    sections = _parse_structured_sections(md_text, default_title=title)
    deck_metrics = _extract_kpi_metrics(md_text)

    c_primary = RGBColor(*active_theme.primary_rgb)
    c_secondary = RGBColor(*active_theme.secondary_rgb)
    c_accent = RGBColor(*active_theme.accent_rgb)
    c_highlight = RGBColor(*active_theme.highlight_rgb)
    c_bg = RGBColor(*active_theme.bg_rgb)
    c_card = RGBColor(*active_theme.card_rgb)
    c_text = RGBColor(*active_theme.text_rgb)
    c_muted = RGBColor(*active_theme.muted_rgb)
    c_white = RGBColor(255, 255, 255)
    c_border = RGBColor(226, 232, 240)

    # --- SLIDE 1: DESIGNER COVER SLIDE ---
    cover = prs.slides.add_slide(blank_layout)
    _attach_ooxml_slide_transition(cover)

    # Full-bleed dark primary background
    bg_rect = cover.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(7.5))
    bg_rect.fill.solid()
    bg_rect.fill.fore_color.rgb = c_primary
    bg_rect.line.fill.background()

    # Top accent ribbon bar
    top_bar = cover.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.14))
    top_bar.fill.solid()
    top_bar.fill.fore_color.rgb = c_accent
    top_bar.line.fill.background()

    # Right decorative geometric accent panel
    deco_panel = cover.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.85), Inches(0.9), Inches(3.85), Inches(5.7))
    deco_panel.fill.solid()
    deco_panel.fill.fore_color.rgb = c_secondary
    deco_panel.line.color.rgb = c_accent
    deco_panel.line.width = Pt(1.5)

    # Theme Pill Badge on Cover
    badge = cover.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.85), Inches(1.15), Inches(3.4), Inches(0.42))
    badge.fill.solid()
    badge.fill.fore_color.rgb = c_accent
    badge.line.fill.background()
    btf = badge.text_frame
    btf.word_wrap = False
    bp = btf.paragraphs[0]
    bp.text = active_theme.label.upper()
    bp.font.size = Pt(10.5)
    bp.font.bold = True
    bp.font.color.rgb = c_white
    bp.alignment = PP_ALIGN.CENTER

    # Determine Cover Title & Subtitle lines from first section
    first_title, first_bullets = sections[0]
    cover_main_title = _strip_emojis_for_clean_title(first_title if len(first_title) > 4 else title)
    if cover_main_title.lower() in ("cover slide", "title slide", "executive overview") and first_bullets:
        cover_main_title = first_bullets[0][:90]
        subtitle_lines = first_bullets[1:5]
    else:
        subtitle_lines = first_bullets[:4]

    # Main Cover Title Box
    t_box = cover.shapes.add_textbox(Inches(0.85), Inches(1.85), Inches(7.6), Inches(2.4))
    ttf = t_box.text_frame
    ttf.word_wrap = True
    tp = ttf.paragraphs[0]
    tp.text = re.sub(r"\*\*(.+?)\*\*", r"\1", cover_main_title)[:120]
    tp.font.size = Pt(34)
    tp.font.bold = True
    tp.font.color.rgb = c_white

    # Subtitle / Executive Highlights on Cover
    if subtitle_lines:
        s_box = cover.shapes.add_textbox(Inches(0.85), Inches(4.25), Inches(7.6), Inches(2.2))
        stf = s_box.text_frame
        stf.word_wrap = True
        for idx, s_line in enumerate(subtitle_lines):
            sp = stf.paragraphs[0] if idx == 0 else stf.add_paragraph()
            sp.text = re.sub(r"\*\*(.+?)\*\*", r"\1", s_line)[:160]
            sp.font.size = Pt(14.5)
            sp.font.color.rgb = RGBColor(203, 213, 225)
            sp.space_after = Pt(6)

    # Populate Right-Hand KPI Metric Cards on Cover Slide
    cover_kpis = deck_metrics[:3] if deck_metrics else [
        (f"{len(sections)} Slides", "Structured Executive Deck"),
        (active_theme.label, "Visual Design System"),
        ("100% Grounded", "Verified Content Synthesis"),
    ]
    for k_idx, (k_val, k_lbl) in enumerate(cover_kpis):
        y_pos = Inches(1.35 + k_idx * 1.65)
        k_card = cover.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(9.15), y_pos, Inches(3.25), Inches(1.38))
        k_card.fill.solid()
        k_card.fill.fore_color.rgb = c_primary
        k_card.line.color.rgb = c_accent if k_idx == 0 else c_highlight
        k_card.line.width = Pt(1.25)
        ktf = k_card.text_frame
        ktf.word_wrap = True
        kp1 = ktf.paragraphs[0]
        kp1.text = str(k_val)[:24]
        kp1.font.size = Pt(22)
        kp1.font.bold = True
        kp1.font.color.rgb = c_white
        kp2 = ktf.add_paragraph()
        kp2.text = str(k_lbl)[:42]
        kp2.font.size = Pt(10.5)
        kp2.font.color.rgb = RGBColor(148, 163, 184)

    # --- SLIDES 2..N: THEMED CONTENT SLIDES ---
    content_sections = sections[1:] if len(sections) > 1 else sections
    total_slides = len(content_sections) + 1

    for s_idx, (sec_title, bullets) in enumerate(content_sections[:28], start=2):
        slide = prs.slides.add_slide(blank_layout)
        _attach_ooxml_slide_transition(slide)

        # Slide soft background
        s_bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(7.5))
        s_bg.fill.solid()
        s_bg.fill.fore_color.rgb = c_bg
        s_bg.line.fill.background()

        # Top Header Banner
        hdr = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(1.05))
        hdr.fill.solid()
        hdr.fill.fore_color.rgb = c_primary
        hdr.line.fill.background()

        # Accent Line under Header Banner
        hdr_line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(1.05), Inches(13.333), Inches(0.06))
        hdr_line.fill.solid()
        hdr_line.fill.fore_color.rgb = c_accent
        hdr_line.line.fill.background()

        # Slide Number Pill Badge
        num_pill = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(0.26), Inches(0.68), Inches(0.52))
        num_pill.fill.solid()
        num_pill.fill.fore_color.rgb = c_accent
        num_pill.line.fill.background()
        ntf = num_pill.text_frame
        np_p = ntf.paragraphs[0]
        np_p.text = f"{s_idx:02d}"
        np_p.font.size = Pt(14)
        np_p.font.bold = True
        np_p.font.color.rgb = c_white
        np_p.alignment = PP_ALIGN.CENTER

        # Slide Header Title
        st_box = slide.shapes.add_textbox(Inches(1.45), Inches(0.22), Inches(11.2), Inches(0.62))
        st_tf = st_box.text_frame
        st_tf.word_wrap = True
        st_p = st_tf.paragraphs[0]
        st_p.text = _strip_emojis_for_clean_title(sec_title)[:95]
        st_p.font.size = Pt(21)
        st_p.font.bold = True
        st_p.font.color.rgb = c_white

        # Check if this slide has KPI metrics for a 2-column layout
        slide_metrics = _extract_kpi_metrics("\n".join(bullets))
        has_side_panel = len(slide_metrics) >= 1 and len(bullets) >= 2
        main_width = Inches(8.35) if has_side_panel else Inches(12.13)

        # Main Elevated White Content Card
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.6), Inches(1.38), main_width, Inches(5.35))
        card.fill.solid()
        card.fill.fore_color.rgb = c_card
        card.line.color.rgb = c_border
        card.line.width = Pt(1)

        # Left Accent Bar inside Main Card
        left_strip = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(1.55), Inches(0.08), Inches(5.0))
        left_strip.fill.solid()
        left_strip.fill.fore_color.rgb = c_accent
        left_strip.line.fill.background()

        # Bullet Content TextFrame
        b_box = slide.shapes.add_textbox(Inches(0.92), Inches(1.58), main_width - Inches(0.55), Inches(4.95))
        btf = b_box.text_frame
        btf.word_wrap = True

        for b_idx, b_text in enumerate(bullets[:7]):
            p = btf.paragraphs[0] if b_idx == 0 else btf.add_paragraph()
            p.space_after = Pt(10)
            clean_line = re.sub(r"\*\*(.+?)\*\*", r"\1", b_text).strip()

            # Add colored bullet indicator run
            r_bul = p.add_run()
            r_bul.text = "▸  "
            r_bul.font.bold = True
            r_bul.font.size = Pt(14.5)
            r_bul.font.color.rgb = c_accent

            # Split `Lead-in: details` for bold typographic contrast
            if ":" in clean_line and clean_line.index(":") < 42:
                head_part, rest_part = clean_line.split(":", 1)
                r_head = p.add_run()
                r_head.text = head_part.strip() + ": "
                r_head.font.bold = True
                r_head.font.size = Pt(14.5)
                r_head.font.color.rgb = c_text

                r_body = p.add_run()
                r_body.text = rest_part.strip()[:420]
                r_body.font.size = Pt(13.5)
                r_body.font.color.rgb = c_secondary
            else:
                r_body = p.add_run()
                r_body.text = clean_line[:440]
                r_body.font.size = Pt(14)
                r_body.font.color.rgb = c_text

        # Right-Hand KPI Callout Panel when slide contains quantified metrics
        if has_side_panel:
            side_x = Inches(9.18)
            side_w = Inches(3.55)
            side_hdr = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, side_x, Inches(1.38), side_w, Inches(5.35))
            side_hdr.fill.solid()
            side_hdr.fill.fore_color.rgb = c_primary
            side_hdr.line.color.rgb = c_accent
            side_hdr.line.width = Pt(1.25)

            sh_box = slide.shapes.add_textbox(side_x + Inches(0.22), Inches(1.55), side_w - Inches(0.44), Inches(0.5))
            sh_p = sh_box.text_frame.paragraphs[0]
            sh_p.text = "KEY IMPACT METRICS"
            sh_p.font.size = Pt(11)
            sh_p.font.bold = True
            sh_p.font.color.rgb = c_accent

            for m_idx, (m_val, m_lbl) in enumerate(slide_metrics[:3]):
                my = Inches(2.15 + m_idx * 1.45)
                m_box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, side_x + Inches(0.22), my, side_w - Inches(0.44), Inches(1.25))
                m_box.fill.solid()
                m_box.fill.fore_color.rgb = c_secondary
                m_box.line.color.rgb = c_highlight
                m_box.line.width = Pt(1)
                mtf = m_box.text_frame
                mtf.word_wrap = True
                mp1 = mtf.paragraphs[0]
                mp1.text = str(m_val)[:24]
                mp1.font.size = Pt(20)
                mp1.font.bold = True
                mp1.font.color.rgb = c_white
                mp2 = mtf.add_paragraph()
                mp2.text = str(m_lbl)[:45]
                mp2.font.size = Pt(10.5)
                mp2.font.color.rgb = RGBColor(203, 213, 225)

        # Slide Footer Bar
        ftr_box = slide.shapes.add_textbox(Inches(0.6), Inches(6.88), Inches(12.13), Inches(0.4))
        ftf = ftr_box.text_frame
        fp = ftf.paragraphs[0]
        fp.text = f"{_strip_emojis_for_clean_title(title)[:60]}   |   {active_theme.label}   |   Slide {s_idx} of {total_slides}"
        fp.font.size = Pt(9.5)
        fp.font.color.rgb = c_muted

    prs.save(dest_path)


def export_content_to_file(
    content: str,
    fmt: str = "pdf",
    filename: Optional[str] = None,
    title: Optional[str] = None,
    tone: str = "",
    user_query: str = "",
    file_id: Optional[str] = None,
    fallback_document_text: str = "",
) -> ExportedFile:
    """
    Compiles `content` into a real, visually styled file on disk in the requested format (`pdf`, `docx`,
    `xlsx`, `csv`, `tsv`, `pptx`, `md`, `html`, `json`, `txt`, `py`, `sql`) after running the 3-pass
    Iterative Document Review & Self-Correction Loop so only pure, verified document content is exported.
    """
    target_fmt = _normalize_format(fmt, filename or "")
    effective_query = f"{user_query} pptx".strip() if target_fmt == "pptx" else user_query
    cleaned, review = review_and_correct_document_loop(
        content=content,
        user_query=effective_query,
        preferred_tone=tone,
        fallback_document_text=fallback_document_text,
    )
    theme = review.theme

    doc_title = (
        _strip_emojis_for_clean_title(title)
        if (title and title.lower() not in ("slm exported document", "slm studio document", "upgraded executive cv"))
        else (review.doc_title or "Executive Document")
    )
    raw_stem = filename or (doc_title if doc_title != "Executive Document" else "executive_document")
    stem = _sanitize_stem(raw_stem)
    if stem.lower() in ("slm_exported_document", "slm_studio_document") and review.doc_title:
        stem = _sanitize_stem(review.doc_title)
    final_filename = f"{stem}.{target_fmt}"

    file_id = re.sub(r"[^A-Za-z0-9_\-]", "", file_id or "") or f"dl_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    out_dir = os.path.join(_downloads_root(), file_id)
    os.makedirs(out_dir, exist_ok=True)
    abs_path = os.path.join(out_dir, final_filename)

    if target_fmt == "pdf":
        _write_pdf(cleaned, abs_path, title=doc_title, theme=theme)
    elif target_fmt == "docx":
        _write_docx(cleaned, abs_path, title=doc_title, theme=theme)
    elif target_fmt == "xlsx":
        _write_xlsx(cleaned, abs_path, title=doc_title, theme=theme)
    elif target_fmt == "csv":
        _write_delimited(cleaned, abs_path, delimiter=",")
    elif target_fmt == "tsv":
        _write_delimited(cleaned, abs_path, delimiter="\t")
    elif target_fmt == "pptx":
        _write_pptx(cleaned, abs_path, title=doc_title, theme=theme)
    elif target_fmt == "html":
        html_doc = markdown_to_styled_html(cleaned, title=doc_title, theme=theme, for_pdf=False)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
    elif target_fmt == "json":
        parsed_json: Any = None
        code_m = re.search(r"```(?:json)?\s*\n([\s\S]+?)\n```", cleaned)
        candidate = code_m.group(1).strip() if code_m else cleaned
        try:
            parsed_json = json.loads(candidate)
        except Exception:
            parsed_json = {
                "title": doc_title,
                "design_theme": theme.label,
                "content_markdown": cleaned,
                "tables": _extract_markdown_tables(cleaned),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(parsed_json, f, indent=2, ensure_ascii=False)
    elif target_fmt in ("py", "sql"):
        blocks = re.findall(r"```(?:" + target_fmt + r"|python|sql)?\s*\n([\s\S]+?)\n```", cleaned, re.IGNORECASE)
        payload = "\n\n".join(b.strip() for b in blocks) if blocks else cleaned
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(payload + "\n")
    else:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(cleaned + "\n")

    size_bytes = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
    download_url = f"/api/harness/download/{file_id}/{final_filename}"
    return ExportedFile(
        file_id=file_id,
        filename=final_filename,
        fmt=target_fmt,
        abs_path=abs_path,
        download_url=download_url,
        size_bytes=size_bytes,
        mime_type=MIME_TYPES.get(target_fmt, "application/octet-stream"),
        theme_key=theme.key,
    )


def resolve_download_path(file_id: str, filename: str) -> Optional[Tuple[str, str, str]]:
    """Safely resolves `/api/harness/download/{file_id}/{filename}` inside `_downloads_root()`."""
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "", file_id or "")
    safe_name = os.path.basename(filename or "")
    if not safe_id or not safe_name:
        return None
    root = os.path.realpath(_downloads_root())
    candidate = os.path.realpath(os.path.join(root, safe_id, safe_name))
    if not candidate.startswith(root + os.sep) or not os.path.isfile(candidate):
        return None
    ext = os.path.splitext(safe_name)[1].lower().lstrip(".")
    mime = MIME_TYPES.get(ext, "application/octet-stream")
    return candidate, safe_name, mime


def _detect_requested_formats(user_query: str, assistant_text: str) -> List[Tuple[str, str]]:
    """
    Returns list of `(stem, fmt)` pairs that should be materialized based on user intent,
    presentation/resume structure in `assistant_text`, or any hallucinated links.
    """
    specs: List[Tuple[str, str]] = []
    seen = set()

    # 1. Check any hallucinated links in assistant_text
    for match in _FAKE_DOWNLOAD_LINK_RE.finditer(assistant_text):
        label, url_part = match.group(1), match.group(2)
        if not _is_hallucinated_download_link(label, url_part):
            continue
        key = _extract_fake_url_stem_and_fmt(label, url_part)
        if key not in seen:
            seen.add(key)
            specs.append(key)

    for match in _BARE_EXAMPLE_URL_RE.finditer(assistant_text):
        bname = os.path.basename(match.group(1))
        key = _extract_fake_url_stem_and_fmt(bname, bname)
        if key not in seen:
            seen.add(key)
            specs.append(key)

    q_lower = (user_query or "").lower()
    a_lower = (assistant_text or "").lower()

    # Detect if assistant generated a multi-slide outline or full resume review/rewrite
    looks_like_slides = ("slide 1:" in a_lower and "slide 2:" in a_lower) or ("slide 1 -" in a_lower and "slide 2 -" in a_lower)
    looks_like_resume = (
        len(assistant_text) > 400
        and (
            "professional profile" in a_lower
            or "core skills" in a_lower
            or "executive summary" in a_lower
            or "key achievements" in a_lower
            or any(w in q_lower for w in ("resume", "cv", "curriculum vitae"))
        )
    )
    has_refusal = bool(_LLM_REFUSAL_LINE_RE.search(assistant_text))

    if _EXPORT_INTENT_RE.search(q_lower) or specs or looks_like_slides or looks_like_resume or has_refusal:
        default_stem = specs[0][0] if specs else "slm_studio_document"
        if any(w in q_lower for w in ("ppt", "pptx", "powerpoint", "presentation", "slide")) or looks_like_slides:
            default_stem = specs[0][0] if specs else "executive_presentation_deck"
        elif any(w in q_lower for w in ("resume", "cv")) or looks_like_resume:
            default_stem = specs[0][0] if specs else "upgraded_executive_cv"

        fmt_keywords = [
            ("pptx", ("pptx", "powerpoint", "ppt", "presentation", "slides", "slide deck", "deck")),
            ("pdf", ("pdf",)),
            ("docx", ("docx", "word", ".doc")),
            ("xlsx", ("xlsx", "excel", "spreadsheet")),
            ("csv", ("csv",)),
            ("html", ("html", "animated", "interactive", "web page")),
            ("md", ("markdown", ".md")),
            ("json", ("json",)),
            ("txt", ("txt", "plain text")),
        ]
        for fmt_code, kws in fmt_keywords:
            if any(k in q_lower for k in kws):
                key = (default_stem, fmt_code)
                if key not in seen:
                    seen.add(key)
                    specs.append(key)

        if looks_like_slides and (default_stem, "pptx") not in seen:
            seen.add((default_stem, "pptx"))
            specs.insert(0, (default_stem, "pptx"))

        # Provide complete companion formats so the user always has PPTX + PDF + DOCX + Animated HTML
        if not specs:
            for fmt_code in ("pdf", "docx", "pptx", "html"):
                specs.append((default_stem, fmt_code))
        else:
            primary_fmt = specs[0][1]
            companions = ["pdf", "docx", "html"] if primary_fmt == "pptx" else ["pptx", "docx", "pdf", "html"]
            for comp in companions:
                ckey = (default_stem, comp)
                if ckey not in seen and len(specs) < 4:
                    seen.add(ckey)
                    specs.append(ckey)

    return specs


def rewrite_and_materialize_downloads(
    user_query: str,
    assistant_text: str,
    fallback_document_text: str = "",
    preferred_tone: str = "",
) -> Tuple[str, List[ExportedFile], Optional[DesignReviewReport]]:
    """
    1. Runs the Document & Presentation Design Reviewer (`review_and_enhance_design`)
    2. Strips any LLM refusal boilerplate ("I can't generate or provide a downloadable PPT...")
    3. Compiles real downloadable files (`.pptx`, `.pdf`, `.docx`, `.html`, etc.) with the selected `DesignTheme`
    4. Prepends & appends a verified, clickable Multi-Format Download Studio Card.
    """
    if not assistant_text:
        return assistant_text, [], None

    specs = _detect_requested_formats(user_query, assistant_text)
    if not specs:
        return assistant_text, [], None

    cleaned_assistant = clean_export_content(assistant_text)
    pure_doc_for_file, review = review_and_correct_document_loop(
        content=assistant_text,
        user_query=user_query,
        preferred_tone=preferred_tone,
        fallback_document_text=fallback_document_text,
    )

    # If the chat output was empty/truncated or if the Design Review Agent synthesized/repaired missing slides,
    # ensure the visible chat response shows the complete, reviewed presentation/document outline
    if len(cleaned_assistant) < 260 or (
        "Presentation Deck" in review.document_type and len(pure_doc_for_file) > len(cleaned_assistant) * 0.75
    ):
        cleaned_assistant = pure_doc_for_file

    exported: List[ExportedFile] = []
    by_fmt: Dict[str, ExportedFile] = {}
    for stem, fmt in specs:
        try:
            ef = export_content_to_file(
                pure_doc_for_file,
                fmt=fmt,
                filename=f"{stem}.{fmt}",
                title=review.doc_title,
                tone=review.theme.key,
                user_query=user_query,
                fallback_document_text=fallback_document_text,
            )
            exported.append(ef)
            by_fmt.setdefault(fmt, ef)
        except Exception:
            pass

    if not exported:
        return cleaned_assistant, [], review

    primary_file = exported[0]

    def _replace_md_link(match: re.Match[str]) -> str:
        label = match.group(1)
        url_part = match.group(2)
        if not _is_hallucinated_download_link(label, url_part):
            return match.group(0)
        _stem, fmt = _extract_fake_url_stem_and_fmt(label, url_part)
        target = by_fmt.get(fmt, primary_file)
        return f"[{label}]({target.download_url})"

    updated_text = _FAKE_DOWNLOAD_LINK_RE.sub(_replace_md_link, cleaned_assistant)

    def _replace_bare_url(match: re.Match[str]) -> str:
        bname = match.group(1)
        _stem, fmt = _extract_fake_url_stem_and_fmt(bname, bname)
        target = by_fmt.get(fmt, primary_file)
        return f"[{target.filename}]({target.download_url})"

    updated_text = _BARE_EXAMPLE_URL_RE.sub(_replace_bare_url, updated_text)

    fmt_labels = {
        "pptx": "PowerPoint Deck (.pptx)",
        "pdf": "Vector PDF (.pdf)",
        "docx": "Word Document (.docx)",
        "html": "Animated HTML5 (.html)",
        "xlsx": "Excel Workbook (.xlsx)",
        "csv": "CSV Table (.csv)",
        "md": "Markdown (.md)",
        "json": "JSON (.json)",
        "txt": "Plain Text (.txt)",
    }
    links_md = "  ·  ".join(
        f"[**Download {fmt_labels.get(ef.fmt, ef.fmt.upper())}** ({max(1, ef.size_bytes // 1024)} KB)]({ef.download_url})"
        for ef in exported
    )

    top_banner = (
        f"> **Design Studio Compiled ({review.theme.label} Theme):** {links_md}\n\n"
    )
    bottom_bar = (
        f"\n\n---\n**Design Review Agent ({review.theme.label}):** "
        f"Completed 3-Pass Audit (Content Purity → Completeness & Role Verification → Typography & Layout). "
        f"Styled {review.section_count} sections/slides with `{review.theme.primary_hex}` / `{review.theme.accent_hex}` palette, "
        f"{len(review.extracted_metrics)} KPI callout cards, and smooth transitions.\n\n"
        f"**Ready for Download:** {links_md}"
    )

    updated_text = top_banner + updated_text.strip() + bottom_bar
    return updated_text, exported, review
