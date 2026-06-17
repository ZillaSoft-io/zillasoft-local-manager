"""Cross-session persistent cache with TTL.

Caches plans, files, and outputs across sessions with automatic expiration.
Stored as JSON in database or file-based SQLite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PersistentCache:
    """Cross-session cache with TTL."""

    def __init__(self, db_path: str | Path = ".cache.db", ttl_hours: int = 24):
        """
        Args:
            db_path: SQLite database path
            ttl_hours: time-to-live for cached entries
        """
        self.db_path = Path(db_path)
        self.ttl_hours = ttl_hours
        self._init_db()
        # Clean up expired entries on startup (prevent unbounded DB growth)
        self.cleanup_expired()

    def _init_db(self) -> None:
        """Initialize cache database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    created_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at ON cache(expires_at)
            """)
        logger.debug(f"Cache initialized at {self.db_path}")

    def _hash_key(self, key: str) -> str:
        """Hash long keys to keep them bounded."""
        if len(key) <= 100:
            return key
        return hashlib.md5(key.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Retrieve value from cache.

        Returns None if not found or expired.
        """
        key = self._hash_key(key)
        now = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT value, hit_count FROM cache WHERE key = ? AND expires_at > ?",
                (key, now),
            )
            row = cur.fetchone()

        if not row:
            return None

        # Increment hit count
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE cache SET hit_count = hit_count + 1 WHERE key = ?",
                (key,),
            )

        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: Any, ttl_hours: Optional[int] = None) -> None:
        """Store value in cache.

        Args:
            key: cache key
            value: value to cache (must be JSON-serializable)
            ttl_hours: optional TTL override
        """
        key = self._hash_key(key)
        ttl = ttl_hours or self.ttl_hours
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=ttl)).isoformat()

        try:
            value_json = json.dumps(value, default=str)
        except (TypeError, ValueError) as e:
            logger.warning(f"Value not JSON-serializable: {e}")
            return

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (key, value_json, now.isoformat(), expires_at),
            )

    def delete(self, key: str) -> None:
        """Delete cache entry."""
        key = self._hash_key(key)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def cleanup_expired(self) -> int:
        """Delete expired entries. Returns count deleted."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM cache WHERE expires_at <= ?",
                (now,),
            )
            deleted = cur.rowcount
        if deleted > 0:
            logger.debug(f"Cache cleanup: deleted {deleted} expired entries")
        return deleted

    def stats(self) -> dict[str, Any]:
        """Cache statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT COUNT(*), SUM(hit_count) FROM cache")
            count, total_hits = cur.fetchone()

        return {
            "entries": count,
            "total_hits": total_hits or 0,
            "avg_hits_per_entry": round((total_hits or 0) / count, 2) if count > 0 else 0,
        }

    def clear(self) -> None:
        """Clear entire cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")
        logger.debug("Cache cleared")


# Global singleton
_persistent_cache: Optional[PersistentCache] = None


def get_persistent_cache(db_path: str | Path = ".cache.db") -> PersistentCache:
    """Get or create global persistent cache."""
    global _persistent_cache
    if _persistent_cache is None:
        _persistent_cache = PersistentCache(db_path)
    return _persistent_cache
