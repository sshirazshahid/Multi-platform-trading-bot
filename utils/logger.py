"""
utils/logger.py — Centralized logging using Loguru.
"""

import os
import sys
from pathlib import Path

from loguru import logger

from config import LOG_LEVEL

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_LOGGER_INITIALIZED = False

# 2026-04-20: Detect pytest runs so test-triggered log events never land in
# the production log files. Previously a pytest session running in parallel
# with the live bot wrote pytest warnings (e.g. synthetic -$99 BTC fixtures)
# into bot_YYYY-MM-DD.log / errors.log and made real losses look catastrophic.
_IN_PYTEST = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def setup_logger():
    global _LOGGER_INITIALIZED
    if _LOGGER_INITIALIZED:
        return logger
    _LOGGER_INITIALIZED = True

    logger.remove()

    logger.add(
        sys.stdout,
        level=LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    if not _IN_PYTEST:
        logger.add(
            str(_LOG_DIR / "bot_{time:YYYY-MM-DD}.log"),
            level="DEBUG",
            rotation="00:00",
            retention="14 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        )

        logger.add(
            str(_LOG_DIR / "errors.log"),
            level="ERROR",
            rotation="10 MB",
            retention="30 days",
            compression="zip",
        )

    return logger


setup_logger()
