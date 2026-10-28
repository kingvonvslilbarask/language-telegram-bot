"""Entry point:  python -m bot.main"""
import logging
import sys

import uvicorn

from .api import create_app
from .config import ConfigError, load_settings


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(str(exc))

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # IMPORTANT: httpx logs every request URL at INFO level, and Telegram API URLs
    # contain the bot token. Keep these loggers quiet so the token never hits the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
