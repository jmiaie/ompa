"""
Token counting with tiktoken (precise) or word-count heuristic (fallback).

Used by lifecycle hooks to report accurate token budgets.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TIKTOKEN_AVAILABLE: bool | None = None  # None = unchecked


def _check_tiktoken() -> bool:
    global _TIKTOKEN_AVAILABLE
    if _TIKTOKEN_AVAILABLE is None:
        try:
            import tiktoken  # noqa: F401
            _TIKTOKEN_AVAILABLE = True
        except ImportError:
            _TIKTOKEN_AVAILABLE = False
    return _TIKTOKEN_AVAILABLE


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """
    Count tokens in text.

    Uses tiktoken when available (pip install ompa[tiktoken]).
    Falls back to a conservative word-count heuristic (~1.3 tokens/word).

    Args:
        text: The text to count tokens in.
        model: tiktoken encoding name or model name. Default is cl100k_base
               (used by GPT-4 / Claude). Pass "gpt2" for older BPE counting.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0

    if _check_tiktoken():
        try:
            import tiktoken

            try:
                enc = tiktoken.encoding_for_model(model)
            except KeyError:
                enc = tiktoken.get_encoding(model)
            return len(enc.encode(text))
        except Exception as e:
            logger.debug("tiktoken counting failed, using heuristic: %s", e)

    # Heuristic: ~1.3 tokens per whitespace-delimited word (conservative for code)
    return int(len(text.split()) * 1.3)


def format_budget(used: int, budget: int) -> str:
    """Return a compact budget string, e.g. '1842 / 2000 (92%)'."""
    pct = int(used / budget * 100) if budget > 0 else 0
    return f"{used} / {budget} ({pct}%)"
