"""
storage.py
----------
SQLite database layer for storing evaluation results.

FIXED: the module-level convenience functions (get_channel_stats,
log_evaluation, get_recent_evaluations) were each creating a fresh
EvaluationStorage() using the hardcoded default "minieval.db" --
completely ignoring config.DB_PATH ("uploads/minieval.db"), which is
the path the live pipeline actually writes to via its injected
EvaluationStorage instance. That meant anything reading through the
module-level functions (e.g. /minieval-stats) was querying an empty,
different database file. They now resolve to config.DB_PATH by default.

Also: is_hallucination was hardcoded to `score < 0.5` instead of using
config.HALLUCINATION_THRESHOLD (0.40 by default) -- now consistent with
the rest of the system's thresholds.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
import logging

import config

logger = logging.getLogger(__name__)


class EvaluationStorage:
    """
    SQLite storage for evaluation results.

    Schema:
        evaluations:
            id INTEGER PRIMARY KEY
            channel TEXT
            thread_ts TEXT
            summary TEXT
            context TEXT
            score REAL
            details TEXT (JSON)
            timestamp TEXT
            is_hallucination BOOLEAN
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: Path to SQLite database file. Defaults to config.DB_PATH
                     so every caller (live pipeline, module-level helpers,
                     the MCP server) reads/writes the same file unless they
                     explicitly choose otherwise.
        """
        self.db_path = db_path or config.DB_PATH

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        logger.info(f"Storage initialized at: {self.db_path}")

    def _init_db(self) -> None:
        """Create the database table if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS evaluations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel TEXT NOT NULL,
                        thread_ts TEXT NOT NULL,
                        summary TEXT,
                        context TEXT,
                        score REAL,
                        details TEXT,
                        timestamp TEXT,
                        is_hallucination BOOLEAN
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_channel_timestamp
                    ON evaluations(channel, timestamp)
                """)
                conn.commit()
                logger.debug("Database initialized successfully")
        except sqlite3.Error as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def save_evaluation(
        self,
        channel: str,
        thread_ts: str,
        summary: str,
        context: str,
        score: float,
        details: Dict[str, Any]
    ) -> Optional[int]:
        """
        Save an evaluation result to the database.

        Returns:
            The ID of the inserted row, or None on failure
        """
        try:
            is_hallucination = score < config.HALLUCINATION_THRESHOLD

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO evaluations
                    (channel, thread_ts, summary, context, score, details, timestamp, is_hallucination)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    channel,
                    thread_ts,
                    summary[:1000],
                    context[:1000],
                    score,
                    json.dumps(details),
                    datetime.now().isoformat(),
                    is_hallucination
                ))
                conn.commit()
                row_id = cursor.lastrowid
                logger.debug(f"Saved evaluation {row_id} for channel {channel}")
                return row_id

        except sqlite3.Error as e:
            logger.error(f"Failed to save evaluation: {e}")
            return None

    def get_channel_stats(self, channel: str, days: int = 30) -> Dict[str, Any]:
        """
        Get evaluation statistics for a channel.

        Returns:
            Dict with total, hallucination_count, hallucination_rate, avg_score
        """
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT COUNT(*), AVG(score), SUM(is_hallucination)
                    FROM evaluations
                    WHERE channel = ? AND timestamp > ?
                """, (channel, cutoff))

                row = cursor.fetchone()
                if not row or row[0] == 0:
                    return {
                        "channel": channel,
                        "total_evaluations": 0,
                        "hallucination_count": 0,
                        "hallucination_rate_pct": 0.0,
                        "average_score": 0.0,
                        "days": days,
                        "trust_score": 100
                    }

                total, avg_score, hall_count = row
                hall_count = hall_count or 0
                hallucination_rate_pct = (hall_count / total) * 100 if total > 0 else 0.0
                trust_score = 100 - hallucination_rate_pct

                return {
                    "channel": channel,
                    "total_evaluations": total,
                    "hallucination_count": hall_count,
                    "hallucination_rate_pct": hallucination_rate_pct,
                    "average_score": avg_score or 0.0,
                    "days": days,
                    "trust_score": trust_score
                }

        except sqlite3.Error as e:
            logger.error(f"Failed to get channel stats: {e}")
            return {"error": str(e)}

    def get_recent_evaluations(
        self,
        limit: int = 10,
        channel: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent evaluations, optionally filtered by channel."""
        try:
            query = """
                SELECT id, channel, thread_ts, summary, context, score, details, timestamp
                FROM evaluations
            """
            params = []

            if channel:
                query += " WHERE channel = ?"
                params.append(channel)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()

                results = []
                for row in rows:
                    results.append({
                        "id": row[0],
                        "channel": row[1],
                        "thread_ts": row[2],
                        "summary": row[3],
                        "context": row[4],
                        "score": row[5],
                        "details": json.loads(row[6]) if row[6] else {},
                        "timestamp": row[7]
                    })

                return results

        except sqlite3.Error as e:
            logger.error(f"Failed to get recent evaluations: {e}")
            return []

    def log_evaluation(
        self,
        channel: str,
        thread_ts: str,
        score: float,
        is_hallucination: bool,
        details: Dict[str, Any]
    ) -> Optional[int]:
        """Legacy alias for save_evaluation. Used by mcp_server.py."""
        return self.save_evaluation(
            channel=channel,
            thread_ts=thread_ts,
            summary=details.get("summary", ""),
            context=details.get("context", ""),
            score=score,
            details=details
        )


# ── Module-level functions for backward compatibility ──────────────────────────
# FIXED: all three now default to config.DB_PATH instead of the literal
# "minieval.db", so they read/write the exact same file the live pipeline
# uses via its injected EvaluationStorage instance.

def get_channel_stats(channel: str, days: int = 30) -> Dict[str, Any]:
    storage = EvaluationStorage(config.DB_PATH)
    return storage.get_channel_stats(channel, days)


def log_evaluation(
    channel: str,
    thread_ts: str,
    score: float,
    is_hallucination: bool,
    details: Dict[str, Any]
) -> Optional[int]:
    storage = EvaluationStorage(config.DB_PATH)
    return storage.log_evaluation(
        channel=channel,
        thread_ts=thread_ts,
        score=score,
        is_hallucination=is_hallucination,
        details=details
    )


def get_recent_evaluations(limit: int = 10, channel: Optional[str] = None) -> List[Dict[str, Any]]:
    storage = EvaluationStorage(config.DB_PATH)
    return storage.get_recent_evaluations(limit=limit, channel=channel)