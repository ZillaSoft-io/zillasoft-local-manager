"""Entry point — launches the Local Manager web server on localhost:5555."""
from __future__ import annotations

import uvicorn

from app.config import ConfigHandler


def main() -> None:
    config = ConfigHandler()
    host = config.get_raw("LOCAL_MANAGER_HOST", "localhost")
    port = config.get("LOCAL_MANAGER_PORT", 5555)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
