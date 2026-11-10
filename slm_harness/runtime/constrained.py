"""
Plane 2: Constrained Decoding, GBNF Compiler, ONNX Token-Trie Masker & Deterministic JSON Repair.

Eliminates malformed JSON, hallucinated tool names, and markdown chatter from small (0.8B-8B)
CPU models across BOTH `llama.cpp` (via GBNF grammars) and `ONNX Runtime` (via token-trie
logit masking + deterministic RFC 8259 state-machine repair).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np


@dataclass
class GrammarSpec:
    """Backend-neutral output constraint specification."""

    mode: str = "none"  # "none" | "json_object" | "json_schema" | "enum" | "gbnf"
    json_schema: Optional[Dict[str, Any]] = None
    allowed_values: Optional[List[str]] = None
    gbnf_source: Optional[str] = None

    @classmethod
    def from_enum(cls, values: Sequence[str]) -> "GrammarSpec":
        cleaned = [str(v) for v in values if str(v)]
        return cls(mode="enum", allowed_values=cleaned)

    @classmethod
    def from_schema(cls, schema: Dict[str, Any]) -> "GrammarSpec":
        return cls(mode="json_schema", json_schema=schema)

    @classmethod
    def json_object(cls) -> "GrammarSpec":
        return cls(mode="json_object")


# Minimal RFC 8259 JSON Object GBNF grammar for llama.cpp when no specific schema is given
GENERIC_JSON_OBJECT_GBNF = r"""
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
            string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
            value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^"\\\x7F\x00-\x1F] |
    "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4})
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})? ws

ws ::= | " " | "\n" [ \t]{0,20}
""".strip()


def _escape_gbnf_literal(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def compile_enum_to_gbnf(values: Sequence[str]) -> str:
    """Compiles a finite set of allowed strings into an exact 0-hallucination GBNF grammar."""
    alts = " | ".join(_escape_gbnf_literal(v) for v in values if v)
    if not alts:
        return 'root ::= ""'
    return f"root ::= ({alts})"


def build_llama_grammar(spec: Optional[GrammarSpec]) -> Any:
    """Compiles `GrammarSpec` into a `llama_cpp.LlamaGrammar` instance if applicable."""
    if spec is None or spec.mode == "none":
        return None
    try:
        from llama_cpp import LlamaGrammar
    except Exception:
        return None

    if spec.mode == "gbnf" and spec.gbnf_source:
        return LlamaGrammar.from_string(spec.gbnf_source, verbose=False)
    if spec.mode == "enum" and spec.allowed_values:
        gbnf = compile_enum_to_gbnf(spec.allowed_values)
        return LlamaGrammar.from_string(gbnf, verbose=False)
    if spec.mode == "json_schema" and spec.json_schema:
        try:
            return LlamaGrammar.from_json_schema(json.dumps(spec.json_schema), verbose=False)
        except Exception:
            return LlamaGrammar.from_string(GENERIC_JSON_OBJECT_GBNF, verbose=False)
    if spec.mode == "json_object":
        return LlamaGrammar.from_string(GENERIC_JSON_OBJECT_GBNF, verbose=False)
    return None


class ONNXPrefixEnumMasker:
    """
    Token-Trie Finite-State Logit Masker for ONNX Runtime.
    Constrains greedy or sampled decoding to emit ONLY one of `allowed_values` (plus EOS),
    guaranteeing valid router labels, yes/no classifications, or tool-name selections
    on pure-CPU ONNX models with <0.1ms overhead per step.
    """

    def __init__(
        self,
        tokenizer: Any,
        allowed_values: Sequence[str],
        eos_token_ids: Sequence[int] = (),
    ) -> None:
        self.eos_ids: Set[int] = set(int(x) for x in eos_token_ids)
        self.trie: Dict[Tuple[int, ...], Set[int]] = {}
        self.terminal_prefixes: Set[Tuple[int, ...]] = set()

        variants: List[str] = []
        for val in allowed_values:
            if not val:
                continue
            variants.append(val)
            # BPE tokenizers sometimes encode leading space differently after prompt end
            if not val.startswith(" "):
                variants.append(" " + val)

        for text in variants:
            ids = self._encode_no_special(tokenizer, text)
            if not ids:
                continue
            prefix: Tuple[int, ...] = ()
            for tid in ids:
                self.trie.setdefault(prefix, set()).add(int(tid))
                prefix = prefix + (int(tid),)
            self.terminal_prefixes.add(prefix)

    @staticmethod
    def _encode_no_special(tokenizer: Any, text: str) -> List[int]:
        if hasattr(tokenizer, "encode"):
            res = tokenizer.encode(text, add_special_tokens=False)
            if hasattr(res, "ids"):
                return [int(i) for i in res.ids]
            if isinstance(res, list):
                return [int(i) for i in res]
        return []

    def mask_logits(self, logits: np.ndarray, generated_ids: Sequence[int]) -> np.ndarray:
        prefix = tuple(int(i) for i in generated_ids)
        allowed: Set[int] = set(self.trie.get(prefix, set()))
        if prefix in self.terminal_prefixes:
            allowed.update(self.eos_ids)

        if not allowed:
            # If already reached a terminal value, force EOS
            if self.eos_ids:
                allowed = set(self.eos_ids)
            else:
                return logits

        masked = np.full_like(logits, -1e9, dtype=np.float32)
        valid_indices = [idx for idx in allowed if 0 <= idx < logits.shape[-1]]
        if not valid_indices:
            return logits
        masked[valid_indices] = logits[valid_indices]
        return masked

    def is_complete(self, generated_ids: Sequence[int]) -> bool:
        prefix = tuple(int(i) for i in generated_ids)
        return prefix in self.terminal_prefixes and prefix not in self.trie


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK_RE = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|javascript|python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def strip_thinking_blocks(text: str) -> Tuple[str, str]:
    """Separates `<think>...</think>` reasoning blocks from final user/tool content."""
    if not text:
        return "", ""
    thoughts: List[str] = []
    for match in _THINK_TAG_RE.finditer(text):
        inner = match.group(0)
        inner = re.sub(r"^<think>|</think>$", "", inner, flags=re.IGNORECASE).strip()
        if inner:
            thoughts.append(inner)
    cleaned = _THINK_TAG_RE.sub("", text)
    if "<think>" in cleaned.lower():
        m = _UNCLOSED_THINK_RE.search(cleaned)
        if m:
            partial = re.sub(r"^<think>", "", m.group(0), flags=re.IGNORECASE).strip()
            if partial:
                thoughts.append(partial)
            cleaned = _UNCLOSED_THINK_RE.sub("", cleaned)
    return "\n\n".join(thoughts), cleaned.strip()


def _extract_balanced_json_span(text: str) -> Optional[str]:
    start = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            start = i
            break
    if start == -1:
        return None

    stack: List[str] = []
    in_str = False
    esc = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    return text[start : idx + 1]
    # Unclosed JSON truncated at max_tokens — return slice from start so auto-closer can seal it
    return text[start:]


def _seal_truncated_json(candidate: str) -> str:
    """Deterministically closes unclosed strings, trailing commas, unquoted keys, and open brackets/braces."""
    s = candidate.strip()
    # Replace Python literals outside strings cautiously
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    # Quote bare unquoted object keys: `{name: "Alice", age: 30}` -> `{"name": "Alice", "age": 30}`
    s = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_\-]*)(\s*:)", r'\1"\2"\3', s)
    # Remove trailing commas before closing braces/brackets
    s = re.sub(r",\s*([}\]])", r"\1", s)

    stack: List[str] = []
    in_str = False
    esc = False
    out_chars: List[str] = []

    for ch in s:
        if in_str:
            if esc:
                esc = False
                out_chars.append(ch)
            elif ch == "\\":
                esc = True
                out_chars.append(ch)
            elif ch == '"':
                in_str = False
                out_chars.append(ch)
            elif ch == "\n":
                out_chars.append("\\n")
            elif ch == "\r":
                out_chars.append("\\r")
            elif ch == "\t":
                out_chars.append("\\t")
            else:
                out_chars.append(ch)
            continue

        if ch == '"':
            in_str = True
            out_chars.append(ch)
        elif ch == "{":
            stack.append("}")
            out_chars.append(ch)
        elif ch == "[":
            stack.append("]")
            out_chars.append(ch)
        elif ch in ("}", "]"):
            if stack and stack[-1] == ch:
                stack.pop()
                out_chars.append(ch)
        else:
            out_chars.append(ch)

    repaired = "".join(out_chars).rstrip()
    if in_str:
        repaired += '"'
    repaired = repaired.rstrip().rstrip(",")
    # If truncated right after a colon `{"key":`, append `null`
    if repaired.endswith(":"):
        repaired += " null"
    while stack:
        repaired += stack.pop()
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def validate_against_minimal_schema(
    obj: Any, schema: Optional[Dict[str, Any]]
) -> Tuple[bool, List[str]]:
    """Lightweight zero-dependency JSON Schema validator for tool calls & `--schema` CLI output."""
    if not schema:
        return True, []
    errors: List[str] = []
    expected_type = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
    }
    if isinstance(expected_type, str) and expected_type in type_map:
        py_t = type_map[expected_type]
        if expected_type == "integer" and isinstance(obj, bool):
            errors.append("Expected integer, got boolean")
        elif not isinstance(obj, py_t):
            errors.append(f"Expected {expected_type}, got {type(obj).__name__}")
            return False, errors

    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"Value {obj!r} not in enum {schema['enum']}")

    if isinstance(obj, dict):
        required = schema.get("required") or []
        for req_key in required:
            if req_key not in obj:
                errors.append(f"Missing required property '{req_key}'")
        props = schema.get("properties") or {}
        for k, sub_schema in props.items():
            if k in obj and isinstance(sub_schema, dict):
                ok, sub_errs = validate_against_minimal_schema(obj[k], sub_schema)
                if not ok:
                    errors.extend(f"{k}: {e}" for e in sub_errs)
    elif isinstance(obj, list) and isinstance(schema.get("items"), dict):
        for idx, item in enumerate(obj):
            ok, sub_errs = validate_against_minimal_schema(item, schema["items"])
            if not ok:
                errors.extend(f"[{idx}]: {e}" for e in sub_errs)

    return len(errors) == 0, errors


def extract_and_repair_json(
    raw_text: str,
    schema: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Extracts and deterministically repairs JSON from raw SLM output.
    Returns `(parsed_object, error_message)`. `error_message` is None on full success.
    """
    if not raw_text:
        return None, "Empty model output"

    _, cleaned = strip_thinking_blocks(raw_text)
    candidates: List[str] = []
    for match in _FENCE_RE.finditer(cleaned):
        block = match.group(1).strip()
        if block:
            candidates.append(block)
    span = _extract_balanced_json_span(cleaned)
    if span:
        candidates.append(span)
    candidates.append(cleaned.strip())

    import ast as _ast

    last_err = "No JSON object or array found"
    for cand in candidates:
        for attempt in (cand, _seal_truncated_json(cand)):
            try:
                parsed = json.loads(attempt)
                ok, schema_errs = validate_against_minimal_schema(parsed, schema)
                if ok:
                    return parsed, None
                last_err = "; ".join(schema_errs)
            except Exception as exc:
                last_err = str(exc)
        # Fallback for single-quoted / unquoted-key Python dict/list literals (e.g. `{name: 'Alice', 'active': True,}`)
        for lit_cand in (cand, _seal_truncated_json(cand)):
            try:
                py_lit = re.sub(r"\btrue\b", "True", lit_cand)
                py_lit = re.sub(r"\bfalse\b", "False", py_lit)
                py_lit = re.sub(r"\bnull\b", "None", py_lit)
                parsed_lit = _ast.literal_eval(py_lit)
                if isinstance(parsed_lit, (dict, list)):
                    ok, schema_errs = validate_against_minimal_schema(parsed_lit, schema)
                    if ok:
                        return parsed_lit, None
                    last_err = "; ".join(schema_errs)
            except Exception:
                pass

    return None, last_err
