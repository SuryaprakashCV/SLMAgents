"""
Plane 3: Content-Addressed Artifact Store (`artifact://<sha12>`) & Structural Descriptor Builder.

Keeps large tool outputs, CSVs, PDFs, SQLite schemas, images, and audio transcripts out of raw
prompt tokens by storing full payloads on disk and injecting compact ~150-token structural
descriptors into the SLM prompt, backed by slice/search inspection.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_config


def extract_openxml_text(abspath: str, ext: str) -> str:
    """
    Zero-dependency OpenXML (.docx / .pptx / .odt) extractor that walks paragraphs,
    tables, headers, footers, and textboxes in reading order so binary ZIP bytes are
    NEVER leaked into the prompt or RAG index.
    """
    parts: List[str] = []
    if ext in (".docx", ".doc"):
        try:
            import docx  # type: ignore

            doc = docx.Document(abspath)
            for p in doc.paragraphs:
                if p.text and p.text.strip():
                    parts.append(p.text.strip())
            for t in doc.tables:
                for row in t.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells if c.text and c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        except Exception:
            pass

    if not parts and zipfile.is_zipfile(abspath):
        try:
            with zipfile.ZipFile(abspath, "r") as zf:
                names = zf.namelist()
                xml_targets: List[str] = []
                if "word/document.xml" in names:
                    xml_targets.extend(sorted(n for n in names if n.startswith("word/header") and n.endswith(".xml")))
                    xml_targets.append("word/document.xml")
                    xml_targets.extend(sorted(n for n in names if n.startswith("word/footer") and n.endswith(".xml")))
                elif any(n.startswith("ppt/slides/slide") and n.endswith(".xml") for n in names):
                    xml_targets.extend(sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")))
                elif "content.xml" in names:
                    xml_targets.append("content.xml")

                for xname in xml_targets:
                    raw_xml = zf.read(xname)
                    root = ET.fromstring(raw_xml)
                    if xname.startswith("word/"):
                        w_p = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
                        w_t = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                        w_tab = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tab"
                        w_br = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
                        for p_node in root.iter(w_p):
                            chunks: List[str] = []
                            for elem in p_node.iter():
                                if elem.tag == w_t and elem.text:
                                    chunks.append(elem.text)
                                elif elem.tag == w_tab:
                                    chunks.append("\t")
                                elif elem.tag == w_br:
                                    chunks.append("\n")
                            line = "".join(chunks).strip()
                            if line:
                                parts.append(line)
                    else:
                        lines = [elem.text.strip() for elem in root.iter() if elem.text and elem.text.strip()]
                        if lines:
                            parts.append("\n".join(lines))
        except Exception:
            pass

    # Deduplicate consecutive identical table/textbox lines while preserving order
    deduped: List[str] = []
    for p in parts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return "\n".join(deduped).strip()


@dataclass
class ArtifactEntry:
    artifact_id: str  # 12-char sha256 hex
    uri: str          # "artifact://<sha12>"
    name: str
    kind: str         # "tabular" | "sqlite" | "pdf" | "image" | "audio" | "code" | "json" | "text"
    source_path: str
    stored_text_path: str
    byte_size: int
    line_count: int
    descriptor: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "uri": self.uri,
            "name": self.name,
            "kind": self.kind,
            "source_path": self.source_path,
            "byte_size": self.byte_size,
            "line_count": self.line_count,
            "descriptor": self.descriptor,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


class ArtifactStore:
    """Content-addressed storage & structural summarizer for large files and tool outputs."""

    INLINE_CHAR_THRESHOLD = 2200

    def __init__(self, root_dir: Optional[str] = None) -> None:
        cfg = get_config()
        self.root_dir = root_dir or os.path.join(cfg.data_dir, "artifacts")
        os.makedirs(self.root_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._index: Dict[str, ArtifactEntry] = {}
        self._load_existing_manifests()

    def _load_existing_manifests(self) -> None:
        if not os.path.isdir(self.root_dir):
            return
        for sub in os.listdir(self.root_dir):
            mpath = os.path.join(self.root_dir, sub, "manifest.json")
            if os.path.isfile(mpath):
                try:
                    with open(mpath, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    entry = ArtifactEntry(
                        artifact_id=d["artifact_id"],
                        uri=d["uri"],
                        name=d["name"],
                        kind=d["kind"],
                        source_path=d["source_path"],
                        stored_text_path=d["stored_text_path"],
                        byte_size=int(d["byte_size"]),
                        line_count=int(d["line_count"]),
                        descriptor=d["descriptor"],
                        metadata=d.get("metadata") or {},
                        created_at=float(d.get("created_at", time.time())),
                    )
                    self._index[entry.artifact_id] = entry
                except Exception:
                    pass

    @staticmethod
    def normalize_id(handle_or_id: str) -> str:
        s = handle_or_id.strip()
        if s.startswith("artifact://"):
            s = s[len("artifact://") :]
        return s.strip("/")

    def get(self, handle_or_id: str) -> Optional[ArtifactEntry]:
        aid = self.normalize_id(handle_or_id)
        with self._lock:
            return self._index.get(aid)

    def list_recent(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._lock:
            vals = sorted(self._index.values(), key=lambda e: e.created_at, reverse=True)
            return [e.to_dict() for e in vals[:limit]]

    def list_artifacts(self, limit: int = 50) -> List[ArtifactEntry]:
        with self._lock:
            vals = sorted(self._index.values(), key=lambda e: e.created_at, reverse=True)
            return list(vals[:limit])

    def ingest_text(
        self,
        content: str,
        name: str = "tool_output.txt",
        kind: str = "text",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ArtifactEntry:
        raw_bytes = content.encode("utf-8", errors="replace")
        aid = hashlib.sha256(raw_bytes).hexdigest()[:12]
        with self._lock:
            if aid in self._index:
                cached = self._index[aid]
                # Re-extract if an older cached .docx/.pptx/.pdf stored raw binary ZIP headers
                if not cached.descriptor or "PK\x03\x04" in cached.descriptor or "\ufffd" in cached.descriptor:
                    self._index.pop(aid, None)
                else:
                    return cached

        adir = os.path.join(self.root_dir, aid)
        os.makedirs(adir, exist_ok=True)
        txt_path = os.path.join(adir, "content.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)

        lines = content.splitlines()
        preview = "\n".join(lines[:14])[:700]
        desc = (
            f"[Artifact artifact://{aid} | {name} | kind={kind} | {len(raw_bytes)} bytes, {len(lines)} lines]\n"
            f"Preview:\n{preview}"
        )

        entry = ArtifactEntry(
            artifact_id=aid,
            uri=f"artifact://{aid}",
            name=name,
            kind=kind,
            source_path=txt_path,
            stored_text_path=txt_path,
            byte_size=len(raw_bytes),
            line_count=len(lines),
            descriptor=desc,
            metadata=metadata or {},
        )
        self._persist_entry(adir, entry)
        return entry

    def ingest_file(self, file_path: str) -> ArtifactEntry:
        abspath = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(abspath):
            raise FileNotFoundError(f"Attachment not found: {file_path}")

        stat = os.stat(abspath)
        hasher = hashlib.sha256()
        hasher.update(f"{abspath}:{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8"))
        with open(abspath, "rb") as bf:
            hasher.update(bf.read(262144))
        aid = hasher.hexdigest()[:12]

        with self._lock:
            if aid in self._index:
                cached = self._index[aid]
                # Re-extract if an older cached .docx/.pptx/.pdf stored raw binary ZIP headers
                if not cached.descriptor or "PK\x03\x04" in cached.descriptor or "\ufffd" in cached.descriptor:
                    self._index.pop(aid, None)
                else:
                    return cached

        adir = os.path.join(self.root_dir, aid)
        os.makedirs(adir, exist_ok=True)
        ext = os.path.splitext(abspath)[1].lower()
        name = os.path.basename(abspath)

        kind, extracted_text, descriptor, meta = self._inspect_file_structure(aid, abspath, name, ext, stat.st_size)
        txt_path = os.path.join(adir, "content.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(extracted_text)

        entry = ArtifactEntry(
            artifact_id=aid,
            uri=f"artifact://{aid}",
            name=name,
            kind=kind,
            source_path=abspath,
            stored_text_path=txt_path,
            byte_size=stat.st_size,
            line_count=max(1, len(extracted_text.splitlines())),
            descriptor=descriptor,
            metadata=meta,
        )
        self._persist_entry(adir, entry)
        return entry

    def _persist_entry(self, adir: str, entry: ArtifactEntry) -> None:
        mpath = os.path.join(adir, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(
                {**entry.to_dict(), "stored_text_path": entry.stored_text_path},
                f,
                indent=2,
                ensure_ascii=False,
            )
        with self._lock:
            self._index[entry.artifact_id] = entry

    def _inspect_file_structure(
        self,
        aid: str,
        abspath: str,
        name: str,
        ext: str,
        byte_size: int,
    ) -> tuple[str, str, str, Dict[str, Any]]:
        uri = f"artifact://{aid}"

        # 1. Tabular Data (CSV, TSV, XLSX, Parquet)
        if ext in (".csv", ".tsv", ".xlsx", ".xls", ".parquet"):
            try:
                import pandas as pd

                if ext == ".csv":
                    df = pd.read_csv(abspath, nrows=5000)
                elif ext == ".tsv":
                    df = pd.read_csv(abspath, sep="\t", nrows=5000)
                elif ext in (".xlsx", ".xls"):
                    df = pd.read_excel(abspath, nrows=5000)
                else:
                    df = pd.read_parquet(abspath)

                cols = [f"{c} ({df[c].dtype})" for c in df.columns[:30]]
                sample = df.head(4).to_string(index=False)
                full_preview = df.head(200).to_csv(index=False)
                desc = (
                    f"[Tabular Artifact {uri} | file={abspath} | rows_sampled={len(df)}, cols={len(df.columns)}]\n"
                    f"Columns: {', '.join(cols)}\n"
                    f"Sample rows:\n{sample}"
                )
                return "tabular", full_preview, desc, {"columns": list(df.columns), "rows": len(df)}
            except Exception:
                pass

        # 2. SQLite Databases
        if ext in (".sqlite", ".sqlite3", ".db"):
            try:
                conn = sqlite3.connect(f"file:{abspath}?mode=ro", uri=True)
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                ]
                ddl_lines: List[str] = []
                table_counts: Dict[str, int] = {}
                for tbl in tables[:25]:
                    ddl_row = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
                    ).fetchone()
                    cnt = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
                    table_counts[tbl] = int(cnt)
                    if ddl_row and ddl_row[0]:
                        ddl_lines.append(f"-- {tbl} ({cnt} rows)\n{ddl_row[0]};")
                conn.close()
                ddl_text = "\n\n".join(ddl_lines)
                desc = (
                    f"[SQLite Database {uri} | path={abspath} | tables={len(tables)}: {', '.join(tables[:15])}]\n"
                    f"Schema DDL:\n{ddl_text[:1400]}"
                )
                return "sqlite", ddl_text, desc, {"tables": tables, "row_counts": table_counts}
            except Exception:
                pass

        # 3. PDF Documents
        if ext == ".pdf":
            try:
                import pymupdf

                doc = pymupdf.open(abspath)
                page_texts: List[str] = []
                for idx in range(min(len(doc), 60)):
                    ptxt = doc[idx].get_text("text").strip()
                    if ptxt:
                        page_texts.append(f"--- [Page {idx + 1}] ---\n{ptxt}")
                total_pages = len(doc)
                doc.close()
                joined = "\n\n".join(page_texts)
                desc = (
                    f"[PDF Document {uri} | file={name} | pages={total_pages} | chars={len(joined)}]\n"
                    f"Page 1 Excerpt:\n{joined[:800]}"
                )
                return "pdf", joined, desc, {"pages": total_pages}
            except Exception:
                pass

        # 3b. Word / PowerPoint / OpenDocument (.docx, .doc, .pptx, .ppt, .odt)
        if ext in (".docx", ".doc", ".pptx", ".ppt", ".odt") or zipfile.is_zipfile(abspath):
            extracted_doc = extract_openxml_text(abspath, ext)
            if extracted_doc:
                doc_lines = extracted_doc.splitlines()
                desc = (
                    f"[Document Artifact {uri} | file={name} | lines={len(doc_lines)} | chars={len(extracted_doc)}]\n"
                    f"Document Excerpt:\n{extracted_doc[:1800]}"
                )
                return "text", extracted_doc, desc, {"lines": len(doc_lines), "chars": len(extracted_doc), "format": ext.lstrip(".")}

        # 4. Images
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
            try:
                from ..runtime import get_runtime

                vis = get_runtime().analyze_image(abspath, ocr_only=True)
                ocr_txt = vis.get("ocr_text", "")
                desc = (
                    f"[Image Artifact {uri} | path={abspath}]\n"
                    f"OCR Preview:\n{ocr_txt[:700] if ocr_txt else '(no embedded text; use vision tool for visual scene Q&A)'}"
                )
                return "image", ocr_txt or f"Image file at {abspath}", desc, {"ocr_chars": len(ocr_txt)}
            except Exception:
                pass

        # 5. Audio files
        if ext in (".wav", ".mp3", ".m4a", ".aiff", ".flac", ".ogg"):
            try:
                from ..runtime import get_runtime

                transcript = get_runtime().transcribe_audio(abspath) or ""
                desc = (
                    f"[Audio Artifact {uri} | path={abspath}]\n"
                    f"Whisper CPU Transcript:\n{transcript[:900]}"
                )
                return "audio", transcript, desc, {"transcript_chars": len(transcript)}
            except Exception:
                pass

        # 6. Source code, JSON, Markdown, Plain text
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(524288)
        except Exception:
            text = ""

        lines = text.splitlines()
        if ext == ".json":
            summary_keys = ""
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    summary_keys = f"Top-level keys ({len(parsed)}): {', '.join(list(parsed.keys())[:20])}"
                elif isinstance(parsed, list):
                    summary_keys = f"JSON Array of {len(parsed)} items"
            except Exception:
                summary_keys = "Unstructured/partial JSON"
            desc = f"[JSON Artifact {uri} | file={name} | {len(lines)} lines | {summary_keys}]\nPreview:\n" + "\n".join(
                lines[:12]
            )[:650]
            return "json", text, desc, {}

        if ext == ".py":
            symbols: List[str] = []
            try:
                tree = ast.parse(text)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        symbols.append(f"class {node.name}")
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(f"def {node.name}()")
            except Exception:
                pass
            sym_info = f"Top symbols: {', '.join(symbols[:20])}\n" if symbols else ""
            desc = (
                f"[Python Source {uri} | path={abspath} | {len(lines)} lines]\n"
                f"{sym_info}Head:\n" + "\n".join(lines[:14])[:650]
            )
            return "code", text, desc, {"symbols": symbols[:30]}

        kind = "code" if ext in (".js", ".ts", ".tsx", ".sql", ".sh", ".go", ".rs", ".java", ".cpp") else "text"
        desc = (
            f"[File Artifact {uri} | path={abspath} | {byte_size} bytes, {len(lines)} lines]\n"
            f"Preview:\n" + "\n".join(lines[:14])[:700]
        )
        return kind, text, desc, {}

    def read_slice(
        self,
        handle_or_id: str,
        start_line: int = 1,
        end_line: int = 100,
        search_pattern: Optional[str] = None,
    ) -> str:
        entry = self.get(handle_or_id)
        if entry is None:
            return f"Artifact not found: {handle_or_id}"
        if not os.path.isfile(entry.stored_text_path):
            return f"Stored payload missing for {entry.uri}"

        with open(entry.stored_text_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

        if search_pattern:
            pat = search_pattern.lower()
            matches: List[str] = []
            for idx, line in enumerate(lines, start=1):
                if pat in line.lower():
                    lo = max(0, idx - 2)
                    hi = min(len(lines), idx + 2)
                    snippet = "\n".join(f"{ln}: {lines[ln - 1]}" for ln in range(lo + 1, hi + 1))
                    matches.append(snippet)
                    if len(matches) >= 12:
                        break
            if not matches:
                return f"No matches for {search_pattern!r} in {entry.uri} ({len(lines)} lines)"
            return f"Matches in {entry.uri} ({entry.name}):\n\n" + "\n---\n".join(matches)

        s = max(1, int(start_line))
        e = min(len(lines), max(s, int(end_line)))
        sliced = "\n".join(f"{ln}: {lines[ln - 1]}" for ln in range(s, e + 1))
        return f"[{entry.uri} ({entry.name}) lines {s}-{e} of {len(lines)}]\n{sliced}"

    def offload_if_large(
        self,
        text: str,
        name: str = "tool_result.txt",
        kind: str = "text",
    ) -> str:
        """Returns `text` inline if compact; otherwise stores an artifact and returns its descriptor."""
        if len(text) <= self.INLINE_CHAR_THRESHOLD:
            return text
        entry = self.ingest_text(text, name=name, kind=kind)
        return (
            f"{entry.descriptor}\n"
            f"(Full output offloaded to {entry.uri}; call `artifact_inspect` with uri='{entry.uri}' to read slices.)"
        )


_GLOBAL_ARTIFACTS: Optional[ArtifactStore] = None


def get_artifact_store() -> ArtifactStore:
    global _GLOBAL_ARTIFACTS
    if _GLOBAL_ARTIFACTS is None:
        _GLOBAL_ARTIFACTS = ArtifactStore()
    return _GLOBAL_ARTIFACTS
