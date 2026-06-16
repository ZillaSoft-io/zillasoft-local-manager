"""Screenshot/attachment storage + vision content-block building (spec §6.1).

Files are saved under {root}/uploads/{session_id}/. Images are inlined into the
Anthropic message as base64 vision blocks so Haiku can read screenshots.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def media_type_for(filename: str) -> str | None:
    return _IMAGE_TYPES.get(Path(filename).suffix.lower())


class AttachmentStore:
    def __init__(self, uploads_dir: os.PathLike | str):
        self.root = Path(uploads_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, filename: str, data: bytes) -> dict[str, Any]:
        # Prevent path traversal; keep only the basename.
        safe = os.path.basename(filename) or "upload"
        folder = self.root / session_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        ref = {
            "filename": safe,
            "path": str(path),
            "media_type": media_type_for(safe),
            "is_image": media_type_for(safe) is not None,
        }
        logger.info("Saved attachment %s for session %s", safe, session_id)
        return ref

    @staticmethod
    def to_content_block(ref: dict[str, Any]) -> dict[str, Any] | None:
        """Build an Anthropic image content block from a saved ref."""
        if not ref.get("is_image"):
            return None
        path = Path(ref["path"])
        if not path.exists():
            return None
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": ref["media_type"],
                "data": data,
            },
        }
