"""Session recovery: view and manage incomplete cycles from crashes.

Provides UI data for showing incomplete sessions and recovery options.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SessionRecoveryManager:
    """Manage recovery of incomplete sessions."""

    def __init__(self, checkpoint_dir: str | Path = ".checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)

    def get_incomplete_sessions(self) -> list[dict]:
        """Get all sessions with incomplete cycles.

        Returns:
            list of incomplete session info dicts
        """
        incomplete = []

        # Scan checkpoint directory for incomplete cycles
        if not self.checkpoint_dir.exists():
            return incomplete

        # Group checkpoints by session_id
        sessions = {}
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            # Parse filename: {session_id}_c{cycle}_{type}.json
            parts = checkpoint_file.stem.split("_")
            if len(parts) >= 2:
                session_id = parts[0]
                cycle_str = parts[1][1:]  # Remove 'c' prefix
                checkpoint_type = "_".join(parts[2:])  # Handle multi-part types

                if session_id not in sessions:
                    sessions[session_id] = {
                        "session_id": session_id,
                        "incomplete_cycles": []
                    }

                sessions[session_id]["incomplete_cycles"].append({
                    "cycle": int(cycle_str),
                    "checkpoint_type": checkpoint_type,
                    "file": str(checkpoint_file)
                })

        # Return sorted list
        return sorted(sessions.values(), key=lambda x: x["session_id"])

    def get_session_details(self, session_id: str, db) -> Optional[dict]:
        """Get recovery details for a specific incomplete session.

        Args:
            session_id: session to recover
            db: database instance

        Returns:
            dict with session recovery info or None if not found
        """
        session = db.get_session(session_id)
        if not session:
            return None

        # Find incomplete cycles
        incomplete_cycles = []
        for checkpoint_file in self.checkpoint_dir.glob(f"{session_id}_*.json"):
            parts = checkpoint_file.stem.split("_")
            if len(parts) >= 2:
                cycle_str = parts[1][1:]
                incomplete_cycles.append(int(cycle_str))

        if not incomplete_cycles:
            return None

        max_incomplete = max(incomplete_cycles)

        return {
            "session_id": session_id,
            "project": session.get("project"),
            "task_type": session.get("task_type"),
            "status": session.get("status"),
            "incomplete_at_cycle": max_incomplete,
            "last_commit": session.get("deployment_status", {}).get("base_sha", "unknown")[:8],
            "action": f"Retry cycle {max_incomplete + 1}",
        }

    def format_for_ui(self, sessions: list[dict]) -> str:
        """Format incomplete sessions for UI display.

        Returns:
            HTML/markdown string for display
        """
        if not sessions:
            return "✓ No incomplete sessions"

        lines = [f"⚠️ {len(sessions)} incomplete session(s):"]
        for sess in sessions:
            first_incomplete = sess["incomplete_cycles"][0]["cycle"]
            lines.append(
                f"  • {sess['session_id'][:8]} ({sess.get('project', 'unknown')}) "
                f"— stopped at cycle {first_incomplete}"
            )

        return "\n".join(lines)


def get_recovery_manager() -> SessionRecoveryManager:
    """Get or create global recovery manager."""
    return SessionRecoveryManager()
