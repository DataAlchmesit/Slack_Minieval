"""
evaluator_bridge.py
-------------------
Core NLI-based faithfulness evaluator.

Adapted from splunk_minieval/src/evaluator_bridge.py for Slack surface.
Logic: treats the original thread/document as the *premise* and the
AI-generated summary as the *hypothesis*.  The entailment probability
from the NLI model becomes the faithfulness score.

Score ∈ [0, 1]
  → Close to 1.0 : summary is faithful to source  ✅
  → Close to 0.0 : summary contradicts / hallucinates ⚠️
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from loguru import logger
from transformers import pipeline

from config import NLI_MODEL, HALLUCINATION_THRESHOLD, VERIFIED_THRESHOLD


# ── Result Dataclass ────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    score: float                        # faithfulness score [0, 1]
    label: str                          # "FAITHFUL" | "UNCERTAIN" | "HALLUCINATED"
    entailment_prob: float
    contradiction_prob: float
    neutral_prob: float
    model: str
    latency_ms: float
    truncated: bool = False             # True if source text was cut for token limit
    error: Optional[str] = None
    raw_scores: dict = field(default_factory=dict)

    @property
    def is_hallucinated(self) -> bool:
        return self.score < HALLUCINATION_THRESHOLD

    @property
    def is_verified(self) -> bool:
        return self.score >= VERIFIED_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "label": self.label,
            "entailment_prob": round(self.entailment_prob, 4),
            "contradiction_prob": round(self.contradiction_prob, 4),
            "neutral_prob": round(self.neutral_prob, 4),
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "truncated": self.truncated,
            "error": self.error,
        }


# ── Model Loader (cached — loads once at startup) ───────────────────────────────

@lru_cache(maxsize=1)
def _load_pipeline():
    logger.info(f"Loading NLI model: {NLI_MODEL}")
    clf = pipeline(
        "text-classification",
        model=NLI_MODEL,
        return_all_scores=True,
        device=-1,          # CPU; change to 0 for GPU
    )
    logger.info("NLI model loaded.")
    return clf


# ── Token Budget ────────────────────────────────────────────────────────────────
# DeBERTa/MNLI models typically cap at 512 tokens.
# We split budget: 380 chars for premise, 128 chars for hypothesis.
# (chars ≠ tokens but ~4 chars/token is a safe heuristic for English)
MAX_PREMISE_CHARS = 1520      # ~380 tokens
MAX_HYPOTHESIS_CHARS = 512    # ~128 tokens


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…", True


# ── Public Evaluation Function ──────────────────────────────────────────────────

def evaluate(source_text: str, summary_text: str) -> EvalResult:
    """Evaluate faithfulness of summary against source."""
    if not source_text or not summary_text:
        return _error_result("source_text and summary_text must be non-empty.")

    t0 = time.perf_counter()
    truncated = False

    try:
        premise, p_trunc = _truncate(source_text, MAX_PREMISE_CHARS)
        hypothesis, h_trunc = _truncate(summary_text, MAX_HYPOTHESIS_CHARS)
        truncated = p_trunc or h_trunc

        clf = _load_pipeline()
        
        # Run inference
        result = clf({"text": premise, "text_pair": hypothesis}, truncation=True, max_length=512)
        
        # ════════════════════════════════════════════════════════════════
        # FIXED: Parse the Hugging Face pipeline output correctly
        # ════════════════════════════════════════════════════════════════
        entailment = 0.0
        contradiction = 0.0
        neutral = 0.0
        
        # The pipeline returns different formats depending on return_all_scores
        # Format with return_all_scores=True: [[{'label': 'ENTAILMENT', 'score': 0.9}, ...]]
        # Format with return_all_scores=False: [{'label': 'ENTAILMENT', 'score': 0.9}]
        
        if isinstance(result, list) and len(result) > 0:
            first_item = result[0]
            
            # Check if it's a list of dicts (return_all_scores=True)
            if isinstance(first_item, list):
                for item in first_item:
                    if isinstance(item, dict) and 'label' in item:
                        label = item.get('label', '').lower()
                        score = item.get('score', 0.0)
                        if 'entail' in label:
                            entailment = score
                        elif 'contradict' in label:
                            contradiction = score
                        elif 'neutral' in label:
                            neutral = score
            # Check if it's a dict (return_all_scores=False)
            elif isinstance(first_item, dict) and 'label' in first_item:
                label = first_item.get('label', '').lower()
                score = first_item.get('score', 0.0)
                if 'entail' in label:
                    entailment = score
                elif 'contradict' in label:
                    contradiction = score
                elif 'neutral' in label:
                    neutral = score
        
        # If still no scores, try direct access
        if entailment == 0.0 and contradiction == 0.0 and neutral == 0.0:
            # Try to find any score in the result
            if isinstance(result, dict) and 'score' in result:
                score = result.get('score', 0.0)
                label = result.get('label', '').lower()
                if 'entail' in label:
                    entailment = score
                elif 'contradict' in label:
                    contradiction = score
                else:
                    neutral = score
            elif isinstance(result, list) and len(result) > 0:
                for item in result:
                    if isinstance(item, dict) and 'score' in item:
                        score = item.get('score', 0.0)
                        label = item.get('label', '').lower()
                        if 'entail' in label:
                            entailment = score
                        elif 'contradict' in label:
                            contradiction = score
                        else:
                            neutral = score
                        break
        
        # Fallback: if all scores are 0, use simple text similarity
        if entailment == 0.0 and contradiction == 0.0 and neutral == 0.0:
            from difflib import SequenceMatcher
            similarity = SequenceMatcher(None, premise.lower(), hypothesis.lower()).ratio()
            entailment = similarity
            contradiction = 1 - similarity
            neutral = 0.0
        
        # Faithfulness score: entailment probability
        faithfulness = max(0.0, entailment - 0.5 * contradiction)
        faithfulness = min(faithfulness, 1.0)
        label = _label(faithfulness)
        latency = (time.perf_counter() - t0) * 1000

        return EvalResult(
            score=faithfulness,
            label=label,
            entailment_prob=entailment,
            contradiction_prob=contradiction,
            neutral_prob=neutral,
            model=NLI_MODEL,
            latency_ms=latency,
            truncated=truncated,
            raw_scores={"entailment": entailment, "contradiction": contradiction, "neutral": neutral},
        )

    except Exception as exc:
        logger.exception(f"Evaluation failed: {exc}")
        return _error_result(str(exc))

# ── Batch Evaluation ────────────────────────────────────────────────────────────

def evaluate_batch(pairs: list[tuple[str, str]]) -> list[EvalResult]:
    """Evaluate multiple (source, summary) pairs."""
    return [evaluate(src, summ) for src, summ in pairs]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _label(score: float) -> str:
    if score >= VERIFIED_THRESHOLD:
        return "FAITHFUL"
    if score < HALLUCINATION_THRESHOLD:
        return "HALLUCINATED"
    return "UNCERTAIN"


def _error_result(msg: str) -> EvalResult:
    return EvalResult(
        score=0.0,
        label="ERROR",
        entailment_prob=0.0,
        contradiction_prob=0.0,
        neutral_prob=0.0,
        model=NLI_MODEL,
        latency_ms=0.0,
        error=msg,
    )