"""
Plane 7: Python AST + Sandboxed Subprocess Verifier, SQLite Live Execution + Fuzzy Column Healer,
and RFC 8259 JSON Schema Verifier.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..governance.policy import run_sandboxed_subprocess
from ..runtime.arena import CancellationToken
from ..runtime.constrained import extract_and_repair_json


@dataclass
class VerificationOutcome:
    passed: bool
    output: Any = None
    cleaned_artifact: str = ""
    error_feedback: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


_PY_FENCE_RE = re.compile(r"```(?:python|py)\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ANY_FENCE_RE = re.compile(r"```[A-Za-z0-9_\-]*\s*(.*?)```", re.DOTALL)
_SQL_FENCE_RE = re.compile(r"```(?:sql|sqlite)\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_STMT_RE = re.compile(r"\b((?:WITH|SELECT)\b[\s\S]+?)(?:;|$)", re.IGNORECASE)
_MISSING_COL_RE = re.compile(r"no such column:\s*([A-Za-z0-9_.\"`]+)", re.IGNORECASE)
_MISSING_TBL_RE = re.compile(r"no such table:\s*([A-Za-z0-9_.\"`]+)", re.IGNORECASE)


def extract_code_block(text: str) -> str:
    if not text:
        return ""
    m = _PY_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    m2 = _ANY_FENCE_RE.search(text)
    if m2:
        return m2.group(1).strip()
    return text.strip()


def verify_python_code(
    code_or_response: str,
    execute: bool = True,
    timeout_sec: float = 12.0,
    cwd: Optional[str] = None,
    prelude: str = "",
    cancel_token: Optional[CancellationToken] = None,
) -> VerificationOutcome:
    code = extract_code_block(code_or_response)
    if not code:
        return VerificationOutcome(
            passed=False,
            error_feedback="No Python code found in output. Emit valid Python inside ```python ... ```.",
        )

    full_script = (prelude.rstrip() + "\n\n" + code) if prelude else code

    try:
        ast.parse(full_script)
    except SyntaxError as syn:
        err_msg = f"SyntaxError at line {syn.lineno}: {syn.msg} -> {(syn.text or '').strip()}"
        return VerificationOutcome(
            passed=False,
            cleaned_artifact=code,
            error_feedback=err_msg,
        )

    if not execute:
        return VerificationOutcome(passed=True, output="AST valid", cleaned_artifact=code)

    fd, tmp_py = tempfile.mkstemp(suffix=".py", prefix="slm_verify_")
    os.close(fd)
    try:
        with open(tmp_py, "w", encoding="utf-8") as f:
            f.write(full_script)

        sub = run_sandboxed_subprocess(
            [sys.executable, tmp_py],
            cwd=cwd,
            timeout_sec=timeout_sec,
            cpu_seconds_limit=max(3, int(timeout_sec)),
            cancel_token=cancel_token,
        )
        if sub.timed_out:
            return VerificationOutcome(
                passed=False,
                cleaned_artifact=code,
                error_feedback=f"Execution timed out after {timeout_sec}s (check for infinite loops).",
                metadata={"duration_ms": sub.duration_ms},
            )
        if sub.returncode != 0:
            tail_err = "\n".join(sub.stderr.strip().splitlines()[-12:])
            return VerificationOutcome(
                passed=False,
                cleaned_artifact=code,
                error_feedback=f"Python runtime error (exit {sub.returncode}):\n{tail_err}",
                metadata={"duration_ms": sub.duration_ms, "stdout": sub.stdout},
            )
        return VerificationOutcome(
            passed=True,
            output=sub.stdout.strip(),
            cleaned_artifact=code,
            metadata={"duration_ms": sub.duration_ms, "stderr": sub.stderr.strip()},
        )
    finally:
        if os.path.exists(tmp_py):
            try:
                os.remove(tmp_py)
            except OSError:
                pass


def extract_sql_query(text: str) -> str:
    if not text:
        return ""
    m = _SQL_FENCE_RE.search(text)
    cand = m.group(1).strip() if m else text.strip()
    if not cand.upper().lstrip().startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN", "CREATE", "ALTER", "INSERT", "UPDATE")):
        m2 = _SQL_STMT_RE.search(cand)
        if m2:
            cand = m2.group(1).strip()
    # Normalize trailing semicolon
    cand = cand.strip().rstrip(";") + ";"
    return cand


def _introspect_sqlite_catalog(conn: sqlite3.Connection) -> Tuple[List[str], Dict[str, List[str]]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    col_map: Dict[str, List[str]] = {}
    for t in tables:
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
            col_map[t] = cols
        except Exception:
            col_map[t] = []
    return tables, col_map


def _build_fuzzy_sql_hint(err_str: str, tables: List[str], col_map: Dict[str, List[str]]) -> str:
    all_cols: List[str] = []
    col_to_tables: Dict[str, List[str]] = {}
    for t, cols in col_map.items():
        for c in cols:
            if c not in all_cols:
                all_cols.append(c)
            col_to_tables.setdefault(c, []).append(t)

    mc = _MISSING_COL_RE.search(err_str)
    if mc:
        bad_raw = mc.group(1).strip('"`')
        bad_col = bad_raw.split(".")[-1]
        suggestions = difflib.get_close_matches(bad_col, all_cols, n=4, cutoff=0.45)
        if suggestions:
            sug_desc = ", ".join(f"{s} (in {'/'.join(col_to_tables[s])})" for s in suggestions)
            return f" Hint: closest valid columns are: {sug_desc}."
        schema_summary = "; ".join(f"{t}({', '.join(cols[:12])})" for t, cols in list(col_map.items())[:8])
        return f" Available schema: {schema_summary}"

    mt = _MISSING_TBL_RE.search(err_str)
    if mt:
        bad_tbl = mt.group(1).strip('"`')
        suggestions = difflib.get_close_matches(bad_tbl, tables, n=3, cutoff=0.4)
        if suggestions:
            return f" Hint: closest valid tables are: {', '.join(suggestions)}."
        return f" Valid tables are: {', '.join(tables)}."

    return ""


def verify_and_execute_sql(
    sql_or_response: str,
    db_path: Optional[str] = None,
    ddl_schema: str = "",
    read_only: bool = True,
    max_rows: int = 50,
) -> VerificationOutcome:
    """
    Validates SQL syntax and executes it against either a live SQLite database (`db_path`, read-only URI)
    or an ephemeral in-memory SQLite instance hydrated from `ddl_schema`.
    Provides fuzzy column/table hints on `OperationalError` for 1-shot SLM self-healing!
    """
    sql = extract_sql_query(sql_or_response)
    if not sql or len(sql) < 7:
        return VerificationOutcome(
            passed=False,
            error_feedback="No valid SQL query found. Return a clean SQLite SELECT query.",
        )

    upper = sql.lstrip().upper()
    if read_only and not upper.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN")):
        return VerificationOutcome(
            passed=False,
            cleaned_artifact=sql,
            error_feedback="Read-only SQL verifier rejected mutating statement (only SELECT / WITH CTEs allowed).",
        )

    conn: Optional[sqlite3.Connection] = None
    try:
        if db_path and os.path.isfile(db_path):
            uri = f"file:{os.path.abspath(db_path)}?mode=ro" if read_only else os.path.abspath(db_path)
            conn = sqlite3.connect(uri, uri=read_only, timeout=10.0)
        else:
            conn = sqlite3.connect(":memory:")
            if ddl_schema.strip():
                # Strip markdown fences if ddl_schema had them
                clean_ddl = extract_code_block(ddl_schema)
                try:
                    conn.executescript(clean_ddl)
                except Exception:
                    # Try statement by statement so partial non-SQLite dialect comments don't abort all tables
                    for stmt in clean_ddl.split(";"):
                        s = stmt.strip()
                        if s.upper().startswith("CREATE"):
                            try:
                                conn.execute(s)
                            except Exception:
                                pass

        tables, col_map = _introspect_sqlite_catalog(conn)

        # Validate query plan first
        stmt_no_semi = sql.rstrip().rstrip(";")
        conn.execute(f"EXPLAIN QUERY PLAN {stmt_no_semi}")

        if db_path and os.path.isfile(db_path):
            cur = conn.execute(stmt_no_semi)
            col_names = [desc[0] for desc in (cur.description or [])]
            rows = cur.fetchmany(max_rows)
            formatted_rows = [dict(zip(col_names, r)) for r in rows]
            return VerificationOutcome(
                passed=True,
                output={"columns": col_names, "rows": formatted_rows, "row_count_sampled": len(rows)},
                cleaned_artifact=sql,
                metadata={"executed_live": True, "db_path": db_path},
            )

        return VerificationOutcome(
            passed=True,
            output={"columns": [], "rows": [], "validated_against_ddl": bool(tables)},
            cleaned_artifact=sql,
            metadata={"executed_live": False},
        )
    except Exception as exc:
        err_txt = str(exc)
        hint = ""
        tables: List[str] = []
        if conn is not None:
            try:
                tables, col_map = _introspect_sqlite_catalog(conn)
                hint = _build_fuzzy_sql_hint(err_txt, tables, col_map)
            except Exception:
                pass
        # If no database or DDL schema was provided (`not tables`), SQLite parses the SQL AST first
        # and then raises `no such table: <tbl>`, which proves the SQL query syntax is 100% valid!
        if not tables and _MISSING_TBL_RE.search(err_txt):
            return VerificationOutcome(
                passed=True,
                output={"columns": [], "rows": [], "validated_against_ddl": False, "syntax_verified": True},
                cleaned_artifact=sql,
                metadata={"executed_live": False, "syntax_verified_no_schema": True},
            )
        return VerificationOutcome(
            passed=False,
            cleaned_artifact=sql,
            error_feedback=f"SQLite error: {err_txt}.{hint}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def verify_json_output(
    raw_text: str,
    schema: Optional[Dict[str, Any]] = None,
) -> VerificationOutcome:
    parsed, err = extract_and_repair_json(raw_text, schema=schema)
    if err is None and parsed is not None:
        import json

        return VerificationOutcome(
            passed=True,
            output=parsed,
            cleaned_artifact=json.dumps(parsed, indent=2, ensure_ascii=False),
        )
    return VerificationOutcome(
        passed=False,
        error_feedback=f"Invalid JSON or schema mismatch: {err}. Output ONLY valid RFC 8259 JSON.",
    )
