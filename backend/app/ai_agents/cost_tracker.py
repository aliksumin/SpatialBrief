"""
Cost Tracker — accumulates token usage across all AI calls in a pipeline run.

Thread-safe: the ensemble classifier runs agents concurrently.
Usage:
    tracker = CostTracker()
    ...
    tracker.add(response, model_name, stage="site_brief")
    ...
    summary = tracker.summary()  # { total_tokens, input_tokens, output_tokens, cost_usd, calls }
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# ── Model pricing (USD per 1M tokens) ──
# Updated periodically. Covers common Gemini models.
# Format: { model_prefix: (input_per_1M, output_per_1M) }
_PRICING: Dict[str, tuple] = {
    "gemini-2.5-flash":    (0.15,  0.60),
    "gemini-2.5-pro":      (1.25, 10.00),
    "gemini-2.0-flash":    (0.10,  0.40),
    "gemini-1.5-flash":    (0.075, 0.30),
    "gemini-1.5-pro":      (1.25,  5.00),
}

def _get_pricing(model_name: str) -> tuple:
    """Find pricing for a model by prefix match."""
    if not model_name:
        return (0.15, 0.60)  # default to flash pricing
    for prefix, pricing in _PRICING.items():
        if model_name.startswith(prefix):
            return pricing
    # Unknown model — use conservative flash pricing
    return (0.15, 0.60)


class CostTracker:
    """Thread-safe accumulator for Gemini API token usage."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: list = []
        self._input_tokens = 0
        self._output_tokens = 0

    def add(
        self,
        response: Any,
        model_name: str = "",
        stage: str = "",
    ) -> None:
        """
        Extract usage_metadata from a Gemini response and accumulate.
        Safe to call from multiple threads.
        """
        if response is None:
            return

        try:
            usage = getattr(response, "usage_metadata", None)
            if usage is None:
                return

            input_tok = getattr(usage, "prompt_token_count", 0) or 0
            output_tok = getattr(usage, "candidates_token_count", 0) or 0

            with self._lock:
                self._input_tokens += input_tok
                self._output_tokens += output_tok
                self._calls.append({
                    "stage": stage,
                    "model": model_name,
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                })

            log.debug("[Cost] %s: +%d in / +%d out tokens (%s)",
                      stage, input_tok, output_tok, model_name)
        except Exception:
            pass  # Never fail the pipeline over cost tracking

    def summary(self) -> Dict[str, Any]:
        """Return accumulated cost summary."""
        with self._lock:
            total_input = self._input_tokens
            total_output = self._output_tokens

        # Calculate cost per call (different models may have different pricing)
        total_cost = 0.0
        for call in self._calls:
            inp_price, out_price = _get_pricing(call.get("model", ""))
            call_cost = (
                call["input_tokens"] * inp_price / 1_000_000
                + call["output_tokens"] * out_price / 1_000_000
            )
            total_cost += call_cost

        return {
            "total_calls": len(self._calls),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "estimated_cost_usd": round(total_cost, 6),
            "calls": self._calls,
        }
