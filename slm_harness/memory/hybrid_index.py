"""
Plane 4 (Tier 3): Hybrid Lexical (SQLite FTS5 BM25) + Dense ONNX Vector + RRF + BGE INT8 Reranker
& Incremental Background `WorkspaceIndexWatcher`.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import get_config
from ..runtime import get_runtime

_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_PAGE_SPLIT_RE = re.compile(r"---\s*\[Page\s+(\d+)\]\s*---", re.IGNORECASE)


def chunk_document_structurally(
    text: str,
    source_name: str,
    chunk_chars: int = 900,
    overlap_chars: int = 160,
) -> List[Dict[str, Any]]:
    """
    Structure-aware chunker preserving exact page numbers (`Page N`), Markdown section headers,
    or source code line ranges (`lines A-B`) for pinpoint `[1], [2]` citations.
    """
    if not text or not text.strip():
        return []

    chunks: List[Dict[str, Any]] = []
    ext = os.path.splitext(source_name)[1].lower()

    # 1. PDF page-delimited text
    if "--- [Page " in text:
        parts = _PAGE_SPLIT_RE.split(text)
        # parts = [pre, page_num1, page_txt1, page_num2, page_txt2, ...]
        idx = 1
        c_idx = 0
        while idx + 1 < len(parts):
            pnum = parts[idx]
            ptxt = parts[idx + 1].strip()
            idx += 2
            if not ptxt:
                continue
            for start in range(0, len(ptxt), max(200, chunk_chars - overlap_chars)):
                sub = ptxt[start : start + chunk_chars].strip()
                if len(sub) >= 24:
                    chunks.append(
                        {
                            "chunk_index": c_idx,
                            "location": f"page {pnum}",
                            "text": sub,
                        }
                    )
                    c_idx += 1
        if chunks:
            return chunks

    # 2. Source Code (line-range chunks)
    if ext in (".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".sql", ".sh", ".cpp", ".c"):
        lines = text.splitlines()
        block_lines = 42
        stride = 34
        c_idx = 0
        for start_ln in range(0, len(lines), stride):
            end_ln = min(len(lines), start_ln + block_lines)
            sub = "\n".join(lines[start_ln:end_ln]).strip()
            if sub:
                chunks.append(
                    {
                        "chunk_index": c_idx,
                        "location": f"lines {start_ln + 1}-{end_ln}",
                        "text": sub,
                    }
                )
                c_idx += 1
            if end_ln >= len(lines):
                break
        return chunks

    # 3. Markdown / Prose (paragraph & heading aware)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    cur_buf: List[str] = []
    cur_len = 0
    cur_heading = ""
    c_idx = 0

    for para in paragraphs:
        if para.startswith("#"):
            first_line = para.splitlines()[0].lstrip("#").strip()[:70]
            if first_line:
                cur_heading = first_line
        if cur_len + len(para) > chunk_chars and cur_buf:
            joined = "\n\n".join(cur_buf)
            chunks.append(
                {
                    "chunk_index": c_idx,
                    "location": f"section '{cur_heading}'" if cur_heading else f"chunk {c_idx + 1}",
                    "text": joined,
                }
            )
            c_idx += 1
            tail = cur_buf[-1][-overlap_chars:] if overlap_chars > 0 else ""
            cur_buf = [tail, para] if tail else [para]
            cur_len = sum(len(x) for x in cur_buf)
        else:
            cur_buf.append(para)
            cur_len += len(para)

    if cur_buf:
        joined = "\n\n".join(cur_buf).strip()
        if joined:
            chunks.append(
                {
                    "chunk_index": c_idx,
                    "location": f"section '{cur_heading}'" if cur_heading else f"chunk {c_idx + 1}",
                    "text": joined,
                }
            )

    return chunks


def build_safe_fts5_query(raw_query: str) -> str:
    """Sanitizes arbitrary natural language into a syntax-error-proof SQLite FTS5 OR query."""
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "what",
        "how",
        "why",
        "who",
        "where",
        "when",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "can",
        "you",
        "please",
        "tell",
        "about",
        "into",
    }
    tokens = [w for w in _WORD_RE.findall(raw_query) if w.lower() not in stopwords]
    if not tokens:
        tokens = _WORD_RE.findall(raw_query)
    if not tokens:
        return ""
    unique: List[str] = []
    for t in tokens[:18]:
        if t.lower() not in [u.lower() for u in unique]:
            unique.append(t)
    return " OR ".join(f'"{t}"' for t in unique)


class HybridVectorFTS:
    """
    Production Pure-CPU Hybrid Retriever:
    SQLite FTS5 BM25 + ONNX Embeddings + Reciprocal Rank Fusion + Neighbor Window + BGE INT8 Reranker.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        cfg = get_config()
        self.db_path = db_path or os.path.join(cfg.data_dir, "hybrid_knowledge.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    mtime REAL NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    indexed_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_docs_col ON documents(collection, source);

                CREATE TABLE IF NOT EXISTS chunks (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    location TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB,
                    embed_dim INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, chunk_index);
                CREATE INDEX IF NOT EXISTS idx_chunks_col ON chunks(collection);

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    source,
                    text,
                    collection UNINDEXED,
                    chunk_rowid UNINDEXED,
                    tokenize = 'porter unicode61'
                );
                """
            )

    def index_text(
        self,
        text: str,
        source: str,
        collection: str = "default",
        mtime: float = 0.0,
        embed: bool = True,
    ) -> Dict[str, Any]:
        if not text or not text.strip():
            return {"indexed": False, "chunks": 0, "source": source}

        content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:20]
        doc_id = hashlib.sha256(f"{collection}::{source}".encode("utf-8")).hexdigest()[:16]

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT content_hash, chunk_count FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if existing is not None and existing["content_hash"] == content_hash:
                return {"indexed": False, "cached": True, "chunks": int(existing["chunk_count"]), "source": source}

        raw_chunks = chunk_document_structurally(text, source_name=source)
        if not raw_chunks:
            return {"indexed": False, "chunks": 0, "source": source}

        embeddings: Optional[np.ndarray] = None
        embed_dim = 0
        if embed:
            try:
                rt = get_runtime()
                vecs = rt.embed_batch([c["text"] for c in raw_chunks], batch_size=16)
                embeddings = vecs
                embed_dim = int(vecs.shape[1]) if vecs.ndim == 2 and vecs.shape[0] > 0 else 0
            except Exception:
                embeddings = None

        now = time.time()
        with self._lock, self._connect() as conn:
            # Remove stale chunks for this doc_id
            old_rows = conn.execute("SELECT row_id FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()
            if old_rows:
                old_ids = [int(r["row_id"]) for r in old_rows]
                qmarks = ",".join("?" for _ in old_ids)
                conn.execute(f"DELETE FROM chunks_fts WHERE chunk_rowid IN ({qmarks})", old_ids)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

            for i, ch in enumerate(raw_chunks):
                emb_blob = (
                    np.asarray(embeddings[i], dtype=np.float32).tobytes()
                    if (embeddings is not None and i < embeddings.shape[0])
                    else None
                )
                cur = conn.execute(
                    """
                    INSERT INTO chunks(doc_id, collection, source, chunk_index, location, text, embedding, embed_dim)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        collection,
                        source,
                        int(ch["chunk_index"]),
                        ch["location"],
                        ch["text"],
                        emb_blob,
                        embed_dim,
                    ),
                )
                rowid = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO chunks_fts(source, text, collection, chunk_rowid) VALUES (?, ?, ?, ?)",
                    (source, ch["text"], collection, rowid),
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO documents(doc_id, collection, source, content_hash, mtime, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, collection, source, content_hash, float(mtime), len(raw_chunks), now),
            )

        return {"indexed": True, "chunks": len(raw_chunks), "source": source, "doc_id": doc_id}

    def index_file(self, file_path: str, collection: str = "workspace") -> Dict[str, Any]:
        abspath = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(abspath):
            return {"indexed": False, "chunks": 0, "error": f"File not found: {file_path}"}

        ext = os.path.splitext(abspath)[1].lower()
        mtime = os.path.getmtime(abspath)
        text = ""
        if ext == ".pdf":
            try:
                import pymupdf

                doc = pymupdf.open(abspath)
                pages = []
                for idx in range(min(len(doc), 80)):
                    t = doc[idx].get_text("text").strip()
                    if t:
                        pages.append(f"--- [Page {idx + 1}] ---\n{t}")
                doc.close()
                text = "\n\n".join(pages)
            except Exception:
                pass
        else:
            try:
                with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(524288)
            except Exception:
                text = ""

        return self.index_text(text, source=abspath, collection=collection, mtime=mtime)

    def search(
        self,
        query: str,
        collections: Optional[Sequence[str]] = None,
        top_k: int = 5,
        use_reranker: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Executes Hybrid FTS5 BM25 + Dense ONNX Vector + RRF + Neighbor Expansion + BGE INT8 Reranking.
        """
        if not query or not query.strip():
            return []

        col_filter_sql = ""
        col_params: List[Any] = []
        if collections:
            placeholders = ",".join("?" for _ in collections)
            col_filter_sql = f" AND collection IN ({placeholders})"
            col_params = list(collections)

        # 1. FTS5 BM25 Lexical Top-24
        fts_q = build_safe_fts5_query(query)
        bm25_ranks: Dict[int, int] = {}
        candidate_rows: Dict[int, Dict[str, Any]] = {}

        with self._lock, self._connect() as conn:
            if fts_q:
                try:
                    fts_sql = (
                        "SELECT chunk_rowid, bm25(chunks_fts) AS score FROM chunks_fts "
                        f"WHERE chunks_fts MATCH ?{col_filter_sql} ORDER BY score ASC LIMIT 24"
                    )
                    fts_hits = conn.execute(fts_sql, [fts_q] + col_params).fetchall()
                    for rank_idx, r in enumerate(fts_hits):
                        rid = int(r["chunk_rowid"])
                        bm25_ranks[rid] = rank_idx
                except Exception:
                    pass

            # 2. Dense ONNX Vector Similarity Top-24
            dense_ranks: Dict[int, int] = {}
            dense_sims: Dict[int, float] = {}
            try:
                q_vec = get_runtime().embed_query(query)
                q_dim = int(q_vec.shape[0])
                vec_sql = (
                    "SELECT row_id, doc_id, collection, source, chunk_index, location, text, embedding, embed_dim "
                    f"FROM chunks WHERE embed_dim = ?{col_filter_sql} LIMIT 2500"
                )
                vec_rows = conn.execute(vec_sql, [q_dim] + col_params).fetchall()
                if vec_rows:
                    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in vec_rows])
                    sims = mat @ q_vec
                    top_n = min(24, sims.shape[0])
                    top_indices = np.argpartition(sims, -top_n)[-top_n:]
                    top_sorted = top_indices[np.argsort(sims[top_indices])[::-1]]
                    for rank_idx, idx_val in enumerate(top_sorted):
                        row = vec_rows[int(idx_val)]
                        rid = int(row["row_id"])
                        dense_ranks[rid] = rank_idx
                        dense_sims[rid] = float(sims[int(idx_val)])
                        candidate_rows[rid] = dict(row)
            except Exception:
                pass

            # Hydrate any BM25-only rows missing from `candidate_rows`
            missing_ids = [rid for rid in bm25_ranks if rid not in candidate_rows]
            if missing_ids:
                qmarks = ",".join("?" for _ in missing_ids)
                mrows = conn.execute(
                    f"SELECT row_id, doc_id, collection, source, chunk_index, location, text FROM chunks WHERE row_id IN ({qmarks})",
                    missing_ids,
                ).fetchall()
                for mr in mrows:
                    candidate_rows[int(mr["row_id"])] = dict(mr)

            if not candidate_rows:
                return []

            # 3. Reciprocal Rank Fusion (RRF k=60)
            rrf_scores: List[Tuple[int, float]] = []
            for rid in candidate_rows:
                score = 0.0
                if rid in bm25_ranks:
                    score += 1.0 / (60.0 + bm25_ranks[rid])
                if rid in dense_ranks:
                    score += 1.15 / (60.0 + dense_ranks[rid])
                rrf_scores.append((rid, score))

            rrf_scores.sort(key=lambda x: x[1], reverse=True)
            shortlist_ids = [rid for rid, _ in rrf_scores[: max(top_k * 2, 10)]]

            # 4. Light neighbor context stitch (+1 following chunk if short table/header)
            shortlist: List[Dict[str, Any]] = []
            for rid in shortlist_ids:
                item = dict(candidate_rows[rid])
                txt = str(item["text"])
                if len(txt) < 380:
                    nxt = conn.execute(
                        "SELECT text FROM chunks WHERE doc_id = ? AND chunk_index = ?",
                        (item["doc_id"], int(item["chunk_index"]) + 1),
                    ).fetchone()
                    if nxt and nxt["text"]:
                        txt = txt + "\n" + str(nxt["text"])[:450]
                item["text"] = txt
                item["rrf_score"] = dict(rrf_scores)[rid]
                item["dense_sim"] = dense_sims.get(rid, 0.0)
                shortlist.append(item)

        # 5. BGE INT8 Cross-Encoder Reranking on CPU
        if use_reranker and len(shortlist) > 1:
            try:
                ranked = get_runtime().rerank(
                    query,
                    [it["text"] for it in shortlist],
                    top_k=top_k,
                )
                final_items: List[Dict[str, Any]] = []
                for cite_idx, (orig_idx, ce_score) in enumerate(ranked, start=1):
                    it = shortlist[orig_idx]
                    final_items.append(
                        {
                            "citation_id": cite_idx,
                            "chunk_id": it["row_id"],
                            "collection": it["collection"],
                            "source": it["source"],
                            "location": it["location"],
                            "score": round(float(ce_score), 4),
                            "text": it["text"],
                        }
                    )
                return final_items
            except Exception:
                pass

        return [
            {
                "citation_id": idx,
                "chunk_id": it["row_id"],
                "collection": it["collection"],
                "source": it["source"],
                "location": it["location"],
                "score": round(float(it["rrf_score"]), 4),
                "text": it["text"],
            }
            for idx, it in enumerate(shortlist[:top_k], start=1)
        ]

    def clear_collection(self, collection: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM chunks_fts WHERE collection = ?", (collection,))
            conn.execute("DELETE FROM chunks WHERE collection = ?", (collection,))
            conn.execute("DELETE FROM documents WHERE collection = ?", (collection,))

    def stats(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            doc_cnt = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chk_cnt = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            cols = [
                dict(r)
                for r in conn.execute(
                    "SELECT collection, COUNT(*) AS chunks FROM chunks GROUP BY collection"
                ).fetchall()
            ]
        return {"documents": int(doc_cnt), "chunks": int(chk_cnt), "collections": cols}


class WorkspaceIndexWatcher:
    """
    Incremental `mtime`-gated workspace document & code indexer.
    Skips `.git`, `.venv`, `models`, `node_modules`, `__pycache__`, and binary blobs so indexing
    a project directory takes minimal CPU and keeps FTS5 + dense retrieval fresh.
    """

    SKIP_DIRS = {
        ".git",
        ".venv",
        "venv",
        "models",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "build",
        "dist",
        ".slm_harness",
    }
    INDEXABLE_EXTS = {".md", ".txt", ".rst", ".yaml", ".yml"}

    def __init__(self, index: Optional[HybridVectorFTS] = None) -> None:
        self.index = index or get_hybrid_index()
        self._bg_thread: Optional[threading.Thread] = None

    def scan_once(
        self,
        root_dir: Optional[str] = None,
        max_files: int = 40,
        include_code: bool = False,
        embed: bool = True,
    ) -> Dict[str, int]:
        root = os.path.abspath(root_dir or get_config().workspace_dir)
        exts = set(self.INDEXABLE_EXTS)
        if include_code:
            exts.update({".py", ".sql", ".js", ".ts"})

        scanned = 0
        updated = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS and not d.startswith(".")]
            for fname in sorted(filenames):
                if scanned >= max_files:
                    return {"scanned": scanned, "updated": updated}
                ext = os.path.splitext(fname)[1].lower()
                if ext not in exts:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    sz = os.path.getsize(fpath)
                    if sz < 20 or sz > 350_000:
                        continue
                    scanned += 1
                    mtime = os.path.getmtime(fpath)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    rel_src = os.path.relpath(fpath, root)
                    res = self.index.index_text(
                        txt,
                        source=rel_src,
                        collection="workspace_docs",
                        mtime=mtime,
                        embed=embed,
                    )
                    if res.get("indexed"):
                        updated += 1
                except Exception:
                    pass
        return {"scanned": scanned, "updated": updated}

    def start_background_sync(self, root_dir: Optional[str] = None) -> None:
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return

        def _worker() -> None:
            try:
                # Lexical FTS5 index first in <80ms so workspace docs are immediately searchable
                self.scan_once(root_dir=root_dir, max_files=35, include_code=False, embed=False)
            except Exception:
                pass

        self._bg_thread = threading.Thread(target=_worker, name="slm-workspace-indexer", daemon=True)
        self._bg_thread.start()


_GLOBAL_HYBRID: Optional[HybridVectorFTS] = None


def get_hybrid_index() -> HybridVectorFTS:
    global _GLOBAL_HYBRID
    if _GLOBAL_HYBRID is None:
        _GLOBAL_HYBRID = HybridVectorFTS()
    return _GLOBAL_HYBRID
