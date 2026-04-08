"""
utils/logger.py — Centralized logging using Loguru.
"""

import sys
from loguru import logger
from config import LOG_LEVEL


def setup_logger():
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

    logger.add(
        "logs/bot_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    logger.add(
        "logs/errors.log",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
    )

    return logger


setup_logger()
