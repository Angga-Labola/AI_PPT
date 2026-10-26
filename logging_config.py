import logging
import os
import sys


def configure_logging() -> logging.Logger:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger("slides_generator")
    logger.setLevel(level)
    logger.propagate = False
    if not any(getattr(handler, "_slides_generator_console", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler._slides_generator_console = True
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
    return logger