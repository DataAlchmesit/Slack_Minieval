"""
app.py
------
Entry point for MiniEval for Slack.
Uses evaluator_bridge.py's evaluate() function (not a class).
"""

import logging
import sys
from pathlib import Path

import config
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Import from evaluator_bridge - it's a FUNCTION, not a class!
from evaluator_bridge import evaluate, EvalResult
from storage import EvaluationStorage
from slack_notifier import SlackNotifier
from pipeline import MiniEvalPipeline
from slack_event_handler import SlackEventHandler

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class MiniEvalWrapper:
    """
    Wrapper that adapts the evaluate() function to the pipeline's expected interface.
    
    Your evaluate() function takes:
        - source_text: the original content (premise)
        - summary_text: the AI summary (hypothesis)
    
    It returns an EvalResult dataclass with:
        - score: faithfulness score [0, 1]
        - is_verified: bool (score >= VERIFIED_THRESHOLD)
        - is_hallucinated: bool (score < HALLUCINATION_THRESHOLD)
        - label: "FAITHFUL" | "UNCERTAIN" | "HALLUCINATED"
        - to_dict(): serializable dict
    """
    
    def __init__(self):
        self.model_name = config.NLI_MODEL
    
    def evaluate(self, summary: str, context: str) -> dict:
        """
        Call your evaluate() function and return a dict.
        
        Args:
            summary: The AI-generated summary (hypothesis)
            context: The original source text (premise)
            
        Returns:
            dict with score, faithful, label, details
        """
        # Your evaluate() expects: evaluate(source_text, summary_text)
        result: EvalResult = evaluate(context, summary)
        
        return {
            "score": result.score,
            "faithful": result.is_verified,      # Uses VERIFIED_THRESHOLD
            "label": result.label,                # FAITHFUL/UNCERTAIN/HALLUCINATED
            "is_hallucinated": result.is_hallucinated,
            "details": result.to_dict(),          # Full details including latency
            "latency_ms": result.latency_ms
        }


def main():
    """Start the MiniEval Slack agent."""
    try:
        logger.info("🚀 Starting MiniEval for Slack...")
        
        # 1. Validate configuration
        if not config.SLACK_BOT_TOKEN or not config.SLACK_APP_TOKEN:
            raise ValueError(
                "Missing Slack tokens. Check your .env file."
            )
        
        # 2. Initialize Slack app
        app = App(
            token=config.SLACK_BOT_TOKEN,
            signing_secret=config.SLACK_SIGNING_SECRET
        )
        
        # 3. Initialize MiniEval components
        logger.info(f"Loading NLI model: {config.NLI_MODEL}")
        evaluator = MiniEvalWrapper()  # Wrapper around your evaluate() function
        storage = EvaluationStorage(db_path=config.DB_PATH)
        notifier = SlackNotifier(token=config.SLACK_BOT_TOKEN)
        
        # 4. Initialize pipeline
        pipeline = MiniEvalPipeline(
            evaluator=evaluator,
            storage=storage,
            notifier=notifier
        )
        
        # 5. Register event handlers
        event_handler = SlackEventHandler(
            app=app,
            pipeline=pipeline,
            storage=storage
        )
        
        # 6. Start the app
        logger.info("⚡ MiniEval for Slack is ready!")
        handler = SocketModeHandler(app, config.SLACK_APP_TOKEN)
        handler.start()
        
    except Exception as e:
        logger.error(f"❌ Failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

@app.command("/minieval-stats")
def handle_stats(ack, command, say):
    ack()
    from storage import EvaluationStorage
    storage = EvaluationStorage()
    stats = storage.get_channel_stats(command["channel"], days=30)
    say(f"📊 MiniEval Stats for {command['channel']}: {stats}")    