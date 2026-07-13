"""
pipeline.py
-----------
Orchestrates the evaluation flow: evaluate -> store -> notify.

FIXED, in order of severity:
  1. `evaluator: MiniEvalEvaluator` in the constructor referenced a class
     name that doesn't exist anywhere -- NameError the instant this module
     was imported. Replaced with a plain duck-typed `Any`.
  2. evaluate() was calling the bare evaluator_bridge.evaluate() function
     directly, with the arguments SWAPPED (summary, context) against its
     real signature (source_text, summary_text) -- scoring everything
     backwards. It now calls self.evaluator.evaluate(summary, context),
     which is the wrapper app.py already built specifically to get this
     order right.
  3. evaluator_bridge.evaluate() returns an EvalResult dataclass, but the
     old code did result["score"] / result.get(...) on it -- dataclasses
     don't support that. Moot now since self.evaluator.evaluate() returns
     a plain dict already.
  4. notify_result() called self.notifier.post_verified(channel=...) /
     post_warning(channel=...) -- SlackNotifier's real parameter is
     channel_id, not channel. Fixed.
  5. Removed the MiniEvalMCPServer import/instantiation entirely. The live
     Slack path calls the evaluator directly; the MCP server is a separate
     standalone process (see mcp_server.py) and was never actually used
     here even before removal -- self.mcp_server was dead code.
  6. notify_result now branches on the 3-way label (FAITHFUL / HALLUCINATED
     / UNCERTAIN / ERROR) instead of a 2-way "faithful" bool, so the
     UNCERTAIN band correctly stays silent instead of always counting as
     "not faithful" -> warning.
"""

import logging
from typing import Dict, Any

import config
from storage import EvaluationStorage
from slack_notifier import SlackNotifier

logger = logging.getLogger(__name__)


class MiniEvalPipeline:
    """Orchestrates the entire evaluation flow."""

    def __init__(self, evaluator: Any, storage: EvaluationStorage, notifier: SlackNotifier):
        """
        Args:
            evaluator: object exposing .evaluate(summary, context) -> dict
                       with keys score/faithful/label/details/latency_ms
                       (see app.py's MiniEvalWrapper).
            storage:   EvaluationStorage instance.
            notifier:  SlackNotifier instance.
        """
        self.evaluator = evaluator
        self.storage = storage
        self.notifier = notifier

        self.hallucination_threshold = config.HALLUCINATION_THRESHOLD
        self.verified_threshold = config.VERIFIED_THRESHOLD

        logger.info(
            f"Pipeline initialized with thresholds: "
            f"hallucination={self.hallucination_threshold:.0%}, "
            f"verified={self.verified_threshold:.0%}"
        )

    def evaluate(self, summary: str, context: str, channel: str, thread_ts: str) -> Dict[str, Any]:
        """
        Run the full evaluation pipeline.

        Returns a plain dict: score, faithful, label, details, channel, thread_ts.
        """
        try:
            logger.debug(f"Evaluating summary in {channel} (thread: {thread_ts})")

            result = self.evaluator.evaluate(summary, context)

            self.storage.save_evaluation(
                channel=channel,
                thread_ts=thread_ts,
                summary=summary,
                context=context[:500],
                score=result["score"],
                details=result["details"],
            )

            logger.info(
                f"Evaluation complete: score={result['score']:.2%}, "
                f"label={result['label']}, "
                f"latency={result.get('latency_ms', 0):.0f}ms"
            )

            return {
                "score": result["score"],
                "faithful": result["faithful"],
                "label": result["label"],
                "details": result["details"],
                "channel": channel,
                "thread_ts": thread_ts,
            }

        except Exception as e:
            logger.error(f"Pipeline evaluation failed: {e}", exc_info=True)
            return {
                "score": 0.0,
                "faithful": False,
                "label": "ERROR",
                "details": {"error": str(e)},
                "channel": channel,
                "thread_ts": thread_ts,
            }

    def notify_result(self, say, result: Dict[str, Any], thread_ts: str) -> None:
        """Send the evaluation result to Slack, branching on the 3-way label."""
        try:
            channel_id = result.get("channel")
            score = result.get("score", 0.0)
            label = result.get("label")
            details = result.get("details", {})

            if label == "FAITHFUL":
                self.notifier.post_verified(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    score=score,
                    details=details,
                )
                logger.debug(f"Posted verified message to {channel_id}")

            elif label == "HALLUCINATED":
                self.notifier.post_warning(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    score=score,
                    details=details,
                    summary_preview="",
                )
                logger.debug(f"Posted warning message to {channel_id}")

            elif label == "UNCERTAIN":
                logger.info(f"Score {score:.2f} is in the uncertain band -- no message posted.")

            else:  # "ERROR" or anything unexpected
                say(text=f"MiniEval evaluation failed: {details.get('error', 'unknown error')}")

        except Exception as e:
            logger.error(f"Failed to notify Slack: {e}", exc_info=True)
            try:
                say(text=f"MiniEval evaluation failed: {str(e)}")
            except Exception as say_error:
                logger.error(f"Failed to send fallback message: {say_error}")