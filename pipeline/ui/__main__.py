"""python -m pipeline.ui [--port] [--processed-root] [--inbox] [--profile]"""
from __future__ import annotations

from typing import Optional, Sequence

import uvicorn

from pipeline.ui.app import create_app
from pipeline.ui.settings import Settings


def main(argv: Optional[Sequence[str]] = None) -> None:
    settings = Settings.from_env_and_args(argv)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
