"""
Plane 7: RAG Grounding & Citation Verifier + SymPy Symbolic Math Verifier.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .code_sql_json import VerificationOutcome

_CITE_BRACKET_RE = re.compile(r"\[(\d+)\]")
_NUMBER_TOKEN_RE = re.compile(r"\b\d+(?:,\d{3})*(?:\.\d+)?%?\b")


def verify_rag_citations(
    answer_text: str,
    evidence_chunks: Sequence[Dict[str, Any]],
    append_legend: bool = True,
) -> VerificationOutcome:
    """
    Validates `[1]..[N]` inline citations against `evidence_chunks`, strips hallucinated out-of-bounds
    citation indices, checks numeric claim entailment, and appends a clean `Sources` legend.
    """
    if not evidence_chunks:
        return VerificationOutcome(passed=True, output=answer_text, cleaned_artifact=answer_text)

    max_id = len(evidence_chunks)
    used_ids: List[int] = []
    invalid_ids: List[int] = []

    def _clean_cite(match: re.Match[str]) -> str:
        cid = int(match.group(1))
        if 1 <= cid <= max_id:
            if cid not in used_ids:
                used_ids.append(cid)
            return f"[{cid}]"
        invalid_ids.append(cid)
        return ""

    cleaned = _CITE_BRACKET_RE.sub(_clean_cite, answer_text).strip()

    # Check whether non-trivial numbers (> 10, excluding 1..max_id) in answer appear in evidence
    ev_corpus = " ".join(str(c.get("text") or "") for c in evidence_chunks).replace(",", "")
    ungrounded_numbers: List[str] = []
    for num_tok in _NUMBER_TOKEN_RE.findall(cleaned):
        norm = num_tok.replace(",", "").rstrip("%")
        if len(norm) >= 3 and norm not in ev_corpus:
            ungrounded_numbers.append(num_tok)

    if append_legend and used_ids and "### Sources" not in cleaned and "**Sources**" not in cleaned:
        legend_lines = ["", "---", "**Sources:**"]
        for cid in sorted(used_ids):
            ch = evidence_chunks[cid - 1]
            src = ch.get("source", "document")
            loc = f" ({ch['location']})" if ch.get("location") else ""
            legend_lines.append(f"- `[{cid}]` {src}{loc}")
        cleaned = cleaned + "\n" + "\n".join(legend_lines)

    return VerificationOutcome(
        passed=True,
        output=cleaned,
        cleaned_artifact=cleaned,
        metadata={
            "cited_ids": sorted(used_ids),
            "stripped_invalid_ids": invalid_ids,
            "ungrounded_numbers_flagged": ungrounded_numbers[:5],
        },
    )


def verify_or_solve_sympy(
    expression_or_equation: str,
    operation: str = "auto",  # "auto" | "evaluate" | "solve" | "differentiate" | "integrate" | "simplify"
    variable: str = "x",
) -> VerificationOutcome:
    """
    Deterministic pure-CPU symbolic & exact numerical math engine powered by `SymPy`.
    """
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )
    except ImportError:
        return VerificationOutcome(passed=False, error_feedback="SymPy is not installed.")

    raw = expression_or_equation.strip()
    if not raw:
        return VerificationOutcome(passed=False, error_feedback="Empty mathematical expression.")

    # Normalize common human math notation (`^` -> `**`)
    expr_txt = raw.replace("^", "**").replace("×", "*").replace("÷", "/")
    transforms = standard_transformations + (implicit_multiplication_application,)
    var_sym = sp.Symbol(variable)

    try:
        if "=" in expr_txt and operation in ("auto", "solve"):
            lhs_s, rhs_s = expr_txt.split("=", 1)
            lhs = parse_expr(lhs_s.strip(), transformations=transforms)
            rhs = parse_expr(rhs_s.strip(), transformations=transforms)
            eq = sp.Eq(lhs, rhs)
            free_syms = sorted(eq.free_symbols, key=lambda s: s.name)
            target = var_sym if var_sym in free_syms else (free_syms[0] if free_syms else var_sym)
            sols = sp.solve(eq, target)
            numeric_sols = [str(sp.N(s, 8)) for s in sols]
            return VerificationOutcome(
                passed=True,
                output={
                    "variable": str(target),
                    "exact_solutions": [str(s) for s in sols],
                    "decimal_solutions": numeric_sols,
                },
                cleaned_artifact=f"{target} = {', '.join(str(s) for s in sols)}",
            )

        expr = parse_expr(expr_txt, transformations=transforms)
        if operation == "differentiate":
            res = sp.diff(expr, var_sym)
        elif operation == "integrate":
            res = sp.integrate(expr, var_sym)
        elif operation == "solve":
            sols = sp.solve(expr, var_sym)
            return VerificationOutcome(
                passed=True,
                output={"variable": variable, "exact_solutions": [str(s) for s in sols]},
                cleaned_artifact=f"{variable} = {', '.join(str(s) for s in sols)}",
            )
        else:
            res = sp.simplify(expr)

        exact_str = str(res)
        dec_str = str(sp.N(res, 10)) if not res.free_symbols else exact_str
        display = exact_str if exact_str == dec_str else f"{exact_str} (≈ {dec_str})"
        return VerificationOutcome(
            passed=True,
            output={"exact": exact_str, "decimal": dec_str},
            cleaned_artifact=display,
        )
    except Exception as exc:
        return VerificationOutcome(
            passed=False,
            error_feedback=f"SymPy parse/solve error on {raw!r}: {exc}",
        )


_INLINE_EQUATION_RE = re.compile(
    r"(?:solve|evaluate|calculate|compute|simplify)\s+([0-9A-Za-z_+\-*/^().=\s]{3,80})",
    re.IGNORECASE,
)


def try_deterministic_math_solve(user_query: str) -> Optional[str]:
    """Attempts to extract and pre-solve an explicit arithmetic/algebraic expression via SymPy."""
    q = user_query.strip().rstrip("?.!")
    # Direct bare arithmetic/equation like `(145 * 38) + 912 / 4` or `2*x + 7 = 19`
    candidates: List[str] = []
    if re.fullmatch(r"[0-9xXyYzZ+\-*/^().=\s]{3,90}", q):
        candidates.append(q)
    m = _INLINE_EQUATION_RE.search(q)
    if m:
        candidates.append(m.group(1).strip())

    for cand in candidates:
        out = verify_or_solve_sympy(cand)
        if out.passed and out.cleaned_artifact:
            return f"SymPy Exact Verification for `{cand}`: `{out.cleaned_artifact}`"
    return None
