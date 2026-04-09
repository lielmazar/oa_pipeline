"""
Shared LLM pricing catalog for llm_classify.py and the API / web UI.

No third-party imports — safe to load in FastAPI without the OpenAI SDK installed.
Source notes: platform.openai.com/docs/pricing (Batch API where applicable).
"""
from __future__ import annotations

# Default model embedded in batch JSONL (see llm_classify.phase_prepare)
MODEL = "gpt-5.4-nano"

# ── Pricing table (per 1M tokens) ─────────────────────────────────────────
# cached_input: price when the prompt prefix is served from the KV cache.
MODEL_PRICING: dict[str, dict] = {
    # ── gpt-5.4 family  (Batch API · short context) ────────────
    "gpt-5.4": {
        "input": 1.25,
        "cached_input": 0.13,
        "output": 7.50,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-mini": {
        "input": 0.375,
        "cached_input": 0.0375,
        "output": 2.25,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-nano": {
        "input": 0.10,
        "cached_input": 0.01,
        "output": 0.625,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API, short context.",
    },
    "gpt-5.4-pro": {
        "input": 15.00,
        "cached_input": None,
        "output": 90.00,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API, short context. No cached-input pricing.",
    },
    # ── previous generation (for comparison) ───────────────────
    "gpt-4o": {
        "input": 1.25,
        "cached_input": None,
        "output": 5.00,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API pricing.",
    },
    "gpt-4o-mini": {
        "input": 0.075,
        "cached_input": None,
        "output": 0.30,
        "reasoning": None,
        "reasoning_est_per_paper": 0,
        "note": "Batch API pricing.",
    },
    "o4-mini": {
        "input": 0.55,
        "cached_input": None,
        "output": 2.20,
        "reasoning": 2.20,
        "reasoning_est_per_paper": 500,
        "note": "Batch API pricing incl. reasoning tokens.",
    },
    "o3": {
        "input": 5.00,
        "cached_input": None,
        "output": 20.00,
        "reasoning": 20.00,
        "reasoning_est_per_paper": 2_000,
        "note": "Batch API pricing incl. reasoning tokens.",
    },
}
