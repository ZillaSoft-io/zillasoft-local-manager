"""Crash recovery system: save progress at critical points, resume from checkpoints.

Saves state before/after risky operations (test execution, reviews).
Detects incomplete cycles and allows resumption without losing work.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """A saved state at a critical point."""
    session_id: str
    cycle_num: int
    checkpoint_type: str  # "pre_test", "post_test", "post_review"
    timestamp: str
    data: dict  # serialized state
    error: Optional[str] = None  # error message if checkpoint is error state


class CrashRecoveryManager:
    """Manages checkpoints and recovery from crashes."""

    def __init__(self, checkpoint_dir: str | Path = ".checkpoints"):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        logger.debug(f"Crash recovery initialized at {self.checkpoint_dir}")

    def _checkpoint_path(self, session_id: str, cycle_num: int,
                        checkpoint_type: str) -> Path:
        """Get path for a checkpoint file."""
        filename = f"{session_id}_c{cycle_num}_{checkpoint_type}.json"
        return self.checkpoint_dir / filename

    def save_checkpoint(
        self,
        session_id: str,
        cycle_num: int,
        checkpoint_type: str,
        data: dict,
        error: Optional[str] = None
    ) -> Path:
        """Save a checkpoint at a critical point.

        Args:
            session_id: session identifier
            cycle_num: cycle number
            checkpoint_type: "pre_test", "post_test", "post_review"
            data: state to save (dict)
            error: error message if this is an error checkpoint

        Returns:
            Path to checkpoint file
        """
        checkpoint = Checkpoint(
            session_id=session_id,
            cycle_num=cycle_num,
            checkpoint_type=checkpoint_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data,
            error=error
        )

        path = self._checkpoint_path(session_id, cycle_num, checkpoint_type)

        try:
            # Atomic write: write to a temp file, fsync, then rename. A crash
            # mid-write leaves the old (or no) checkpoint intact rather than a
            # truncated/corrupt file — critical for a crash-recovery system.
            tmp = path.with_suffix(".json.tmp")
            payload = {
                "session_id": checkpoint.session_id,
                "cycle_num": checkpoint.cycle_num,
                "checkpoint_type": checkpoint.checkpoint_type,
                "timestamp": checkpoint.timestamp,
                "data": checkpoint.data,
                "error": checkpoint.error,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)

            if error:
                logger.error(f"Saved ERROR checkpoint: {path} ({error})")
            else:
                logger.debug(f"Saved checkpoint: {path}")

            return path

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            raise

    def load_checkpoint(
        self, session_id: str, cycle_num: int, checkpoint_type: str
    ) -> Optional[Checkpoint]:
        """Load a checkpoint if it exists.

        Returns:
            Checkpoint or None if not found
        """
        path = self._checkpoint_path(session_id, cycle_num, checkpoint_type)

        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return Checkpoint(
                session_id=data["session_id"],
                cycle_num=data["cycle_num"],
                checkpoint_type=data["checkpoint_type"],
                timestamp=data["timestamp"],
                data=data["data"],
                error=data.get("error"),
            )

        except Exception as e:
            logger.warning(f"Failed to load checkpoint {path}: {e}")
            return None

    def detect_incomplete_cycle(
        self, session_id: str, cycle_num: int
    ) -> tuple[bool, Optional[str]]:
        """Detect if a cycle crashed mid-execution.

        Returns:
            (is_incomplete, last_checkpoint_type)
            - is_incomplete: True if cycle didn't complete
            - last_checkpoint_type: the checkpoint where it stopped ("pre_test", etc.)
        """
        # Check in order of execution
        for checkpoint_type in ["pre_test", "post_test", "post_review"]:
            checkpoint = self.load_checkpoint(session_id, cycle_num, checkpoint_type)
            if checkpoint is None:
                # This checkpoint doesn't exist, so cycle didn't reach here
                return (True, None if checkpoint_type == "pre_test" else checkpoint_type.replace("post_", ""))

        # All checkpoints exist, cycle is complete
        return (False, None)

    def cleanup_cycle_checkpoints(self, session_id: str, cycle_num: int) -> None:
        """Clean up checkpoints for a completed cycle.

        Args:
            session_id: session identifier
            cycle_num: cycle number to clean up
        """
        for checkpoint_type in ["pre_test", "post_test", "post_review"]:
            path = self._checkpoint_path(session_id, cycle_num, checkpoint_type)
            if path.exists():
                try:
                    path.unlink()
                    logger.debug(f"Cleaned up checkpoint: {path}")
                except Exception as e:
                    logger.warning(f"Failed to clean checkpoint {path}: {e}")

    def cleanup_session_checkpoints(self, session_id: str) -> None:
        """Clean up all checkpoints for a session.

        Args:
            session_id: session identifier
        """
        for checkpoint_file in self.checkpoint_dir.glob(f"{session_id}_*.json"):
            try:
                checkpoint_file.unlink()
                logger.debug(f"Cleaned up checkpoint: {checkpoint_file}")
            except Exception as e:
                logger.warning(f"Failed to clean checkpoint {checkpoint_file}: {e}")

    def cleanup_old_checkpoints(self, max_age_hours: int = 24) -> None:
        """Optimization 4: Clean up old checkpoints to prevent disk bloat.

        Keeps only the latest post_review checkpoint per session (for recovery).
        Deletes all older failed attempts.

        Args:
            max_age_hours: Delete checkpoints older than this
        """
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        # Group checkpoints by session
        sessions: dict[str, list[Path]] = {}
        for checkpoint_file in self.checkpoint_dir.glob("*.json"):
            # Parse session_id from filename: {session_id}_c{cycle}_{type}.json
            parts = checkpoint_file.stem.split("_")
            if len(parts) >= 2:
                session_id = parts[0]
                if session_id not in sessions:
                    sessions[session_id] = []
                sessions[session_id].append(checkpoint_file)

        # For each session, keep only the latest post_review checkpoint
        for session_id, checkpoints in sessions.items():
            # Sort by modification time, keep newest
            sorted_checkpoints = sorted(checkpoints, key=lambda p: p.stat().st_mtime, reverse=True)

            for checkpoint_file in sorted_checkpoints[1:]:  # Skip the newest
                try:
                    # Only delete if older than cutoff
                    mtime = datetime.fromtimestamp(
                        checkpoint_file.stat().st_mtime, tz=timezone.utc
                    )
                    if mtime < cutoff:
                        checkpoint_file.unlink()
                        logger.debug(f"Cleaned up old checkpoint: {checkpoint_file}")
                except Exception as e:
                    logger.warning(f"Failed to clean old checkpoint {checkpoint_file}: {e}")


# Global recovery manager
_recovery: CrashRecoveryManager | None = None


def get_crash_recovery() -> CrashRecoveryManager:
    """Get or create global crash recovery manager."""
    global _recovery
    if _recovery is None:
        _recovery = CrashRecoveryManager()
    return _recovery
