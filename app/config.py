"""ConfigHandler — tiered read/write access to the Local Manager .env file.

Design goals (from LOCAL_MANAGER_ARCHITECTURE.md §2.2):
  * All three agents can READ the full config.
  * Write access is tiered:
      - Credentials  -> Mario only (UI). Agent writes raise CredentialWriteError.
      - Project / model / manager / notification settings -> agent-writable.
  * Typed reads with schema validation (warn on enum/type mismatch).
  * File formatting (comments, blank lines, ordering) is preserved.
  * Writes are atomic (.tmp + os.replace) per the ZillaSoft convention.

The handler is the single source of truth for config; it also mirrors values
into os.environ so libraries that read the environment stay in sync. It does
NOT use python-dotenv's parser for writes, so Windows backslash paths are kept
verbatim (no escape processing).
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Optional

from .errors import ConfigError, ConfigValidationError, CredentialWriteError

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Credential classification — these keys are Mario-only (UI). Agents may read
# but not write them; a write attempt raises CredentialWriteError.
# --------------------------------------------------------------------------- #
_CREDENTIAL_EXACT = frozenset({
    "ANTHROPIC_API_KEY",
    "SENTRY_AUTH_TOKEN",
    "JIRA_API_TOKEN",
    "GITHUB_TOKEN",
    "RAILWAY_API_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "BREVO_API_KEY",
    "LOCAL_MANAGER_AUTH_TOKEN",
})
_CREDENTIAL_PREFIXES = ("STRIPE_", "AUTH0_")

# Sentinels used in the .env for values that are not yet set.
_UNSET_SENTINELS = frozenset({"", "<FILL>", "<AUTO>"})

# --------------------------------------------------------------------------- #
# Lightweight schema for typed reads + validation. Keys not covered here are
# returned as plain strings. Enums warn (not raise) on read; writes of an
# invalid enum value raise ConfigValidationError.
# --------------------------------------------------------------------------- #
_ENUMS = {
    "ANTHROPIC_EFFORT_HAIKU": {"low", "medium", "high"},
    "ANTHROPIC_EFFORT_SONNET": {"low", "medium", "high"},
    "ANTHROPIC_EFFORT_OPUS": {"low", "medium", "high"},
}
# Suffix-based enum rules (apply to any matching PROJECT_* key).
_ENUM_SUFFIXES = {
    "_HEALTH_CHECK_FORMAT": {"json", "html"},
}

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _infer_type(key: str) -> str:
    """Return 'bool' | 'int' | 'float' | 'str' for a config key."""
    if key in ("LOCAL_MANAGER_ROLLBACK_REQUIRE_APPROVAL",
               "LOCAL_MANAGER_AUTO_COMMIT",
               "LOCAL_MANAGER_AUTO_DEPLOY") or \
            key.startswith("NOTIFICATIONS_") and (
                key.endswith("_ENABLED") or "_ON_" in key):
        return "bool"
    if key in ("LOCAL_MANAGER_PORT", "LOCAL_MANAGER_MAX_CYCLES",
               "LOCAL_MANAGER_PAUSE_EXPIRY_DAYS", "JIRA_BOARD_ID") or \
            key.endswith("_HEALTH_CHECK_EXPECTED_STATUS"):
        return "int"
    if key in ("LOCAL_MANAGER_MONTHLY_COST_CAP",
               "LOCAL_MANAGER_CURRENT_MONTH_SPENT"):
        return "float"
    return "str"


def _allowed_enum(key: str) -> Optional[set]:
    if key in _ENUMS:
        return _ENUMS[key]
    for suffix, choices in _ENUM_SUFFIXES.items():
        if key.endswith(suffix):
            return choices
    return None


class ConfigHandler:
    """Reads and writes the Local Manager .env with tiered permissions."""

    def __init__(self, env_path: Optional[os.PathLike | str] = None):
        self.root = Path(__file__).resolve().parents[1]
        self.env_path = Path(env_path) if env_path else self.root / ".env"
        self._lock = threading.RLock()
        self._values: dict[str, str] = {}
        if not self.env_path.exists():
            raise ConfigError(
                f".env not found at {self.env_path}. "
                f"Copy .env.example to .env and fill it in."
            )
        self.reload()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        """Re-read the .env file into memory and mirror into os.environ."""
        with self._lock:
            text = self.env_path.read_text(encoding="utf-8")
            values: dict[str, str] = {}
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
            self._values = values
            for k, v in values.items():
                os.environ[k] = v
        logger.debug("Loaded %d config keys from %s", len(self._values), self.env_path)

    # ------------------------------------------------------------------ #
    # Classification
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_credential(key: str) -> bool:
        return key in _CREDENTIAL_EXACT or key.startswith(_CREDENTIAL_PREFIXES)

    @staticmethod
    def is_set(value: Optional[str]) -> bool:
        return value is not None and value.strip() not in _UNSET_SENTINELS

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get_raw(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return the raw string value (no type coercion)."""
        with self._lock:
            return self._values.get(key, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the typed, validated value, or `default` if unset/missing."""
        with self._lock:
            raw = self._values.get(key)
        if raw is None or raw.strip() in _UNSET_SENTINELS:
            return default

        # Enum check (warn-only on read).
        choices = _allowed_enum(key)
        if choices is not None and raw not in choices:
            logger.warning(
                "Config '%s' = %r is not one of %s; returning raw value.",
                key, raw, sorted(choices),
            )
            return raw

        kind = _infer_type(key)
        try:
            if kind == "bool":
                low = raw.strip().lower()
                if low in _TRUE:
                    return True
                if low in _FALSE:
                    return False
                logger.warning("Config '%s' = %r is not a valid bool.", key, raw)
                return default
            if kind == "int":
                return int(raw)
            if kind == "float":
                return float(raw)
        except ValueError:
            logger.warning(
                "Config '%s' = %r could not be coerced to %s.", key, raw, kind)
            return default
        return raw

    def require(self, key: str) -> str:
        """Return a value that must be set, else raise. Useful for credentials."""
        raw = self.get_raw(key)
        if not self.is_set(raw):
            raise ConfigValidationError(
                f"Required config '{key}' is not set "
                f"(value: {raw!r}). Set it via the UI settings panel."
            )
        return raw  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def set(self, key: str, value: Any, actor: str = "agent") -> None:
        """Write a config value.

        actor='agent' (default) enforces the credential write block.
        actor='system' is the internal path used by the app itself (e.g. to
        generate the auth token or reset monthly spend); it bypasses the block
        but still validates.
        """
        if actor != "system" and self.is_credential(key):
            raise CredentialWriteError(key)

        value = "" if value is None else str(value)
        self._validate(key, value)

        with self._lock:
            self._persist(key, value)
            self._values[key] = value
            os.environ[key] = value
        logger.info("Config '%s' updated by %s.", key, actor)

    def _validate(self, key: str, value: str) -> None:
        """Type/enum validation on write. Raises ConfigValidationError."""
        if value.strip() in _UNSET_SENTINELS:
            return  # allow clearing / sentinels

        choices = _allowed_enum(key)
        if choices is not None and value not in choices:
            raise ConfigValidationError(
                f"'{key}' must be one of {sorted(choices)}, got {value!r}.")

        kind = _infer_type(key)
        try:
            if kind == "bool":
                if value.strip().lower() not in (_TRUE | _FALSE):
                    raise ValueError
            elif kind == "int":
                int(value)
            elif kind == "float":
                float(value)
        except ValueError:
            raise ConfigValidationError(
                f"'{key}' must be a {kind}, got {value!r}.")

    def _persist(self, key: str, value: str) -> None:
        """Atomically rewrite the .env, preserving comments and ordering.

        Re-reads the file each time so concurrent edits aren't clobbered.
        Updates the matching `KEY=` line in place, or appends if absent.
        """
        lines = self.env_path.read_text(encoding="utf-8").splitlines()
        pattern = re.compile(r"^(\s*)" + re.escape(key) + r"(\s*)=")
        found = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")

        tmp = self.env_path.with_name(self.env_path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.env_path)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def resolve_path(self, key: str, default: Optional[str] = None) -> Path:
        """Resolve a path-valued config key relative to the project root."""
        raw = self.get_raw(key, default)
        if raw is None:
            raise ConfigValidationError(f"Path config '{key}' is not set.")
        p = Path(raw)
        return p if p.is_absolute() else (self.root / p).resolve()

    def ensure_auth_token(self) -> str:
        """Generate LOCAL_MANAGER_AUTH_TOKEN on first run if unset; return it."""
        current = self.get_raw("LOCAL_MANAGER_AUTH_TOKEN")
        if self.is_set(current):
            return current  # type: ignore[return-value]
        token = secrets.token_urlsafe(32)
        self.set("LOCAL_MANAGER_AUTH_TOKEN", token, actor="system")
        logger.info("Generated a new LOCAL_MANAGER_AUTH_TOKEN.")
        return token

    def snapshot(self, redact_credentials: bool = True) -> dict[str, Any]:
        """Return a config snapshot for the UI. Credentials are masked."""
        with self._lock:
            items = dict(self._values)
        out: dict[str, Any] = {}
        for key, raw in items.items():
            if redact_credentials and self.is_credential(key):
                out[key] = "<set>" if self.is_set(raw) else "<unset>"
            else:
                out[key] = raw
        return out
