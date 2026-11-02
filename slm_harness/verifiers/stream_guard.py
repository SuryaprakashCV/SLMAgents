"""
Plane 7: Live Streaming Repetition & Degeneration Guard (`StreamDegenerationGuard`).

Detects runaway token repetition loops (character floods or repeated multi-word phrases)
in real time on small quantized models and cleanly trims the repeated tail.
"""

from __future__ import annotations


class StreamDegenerationGuard:
    def __init__(self, window_chars: int = 800, phrase_len: int = 36, max_repeats: int = 4) -> None:
        self.window_chars = window_chars
        self.phrase_len = phrase_len
        self.max_repeats = max_repeats
        self._text = ""
        self.triggered = False

    def push(self, piece: str) -> bool:
        """Appends `piece`; returns `True` if degeneration is detected and generation should stop."""
        if not piece or self.triggered:
            return self.triggered
        self._text += piece
        tail = self._text[-self.window_chars :]

        # 1. Single-character runaway flood (e.g. 90 identical non-space chars)
        if len(tail) >= 96:
            last_ch = tail[-1]
            if last_ch not in (" ", "\n", "-", "=") and tail[-90:] == (last_ch * 90):
                self.triggered = True
                return True

        # 2. Exact suffix phrase repeated `max_repeats` times consecutively or densely
        if len(tail) >= self.phrase_len * self.max_repeats:
            suffix = tail[-self.phrase_len :]
            if len(suffix.strip()) >= 14 and tail.count(suffix) >= self.max_repeats:
                self.triggered = True
                return True

        return False

    def clean_text(self) -> str:
        if not self.triggered:
            return self._text
        # Trim repeated suffix blocks back to first clean occurrence
        txt = self._text
        suffix = txt[-self.phrase_len :]
        first_pos = txt.find(suffix, max(0, len(txt) - self.window_chars))
        if first_pos != -1 and first_pos + self.phrase_len < len(txt):
            return txt[: first_pos + self.phrase_len].rstrip()
        return txt.rstrip()
