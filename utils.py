"""
utils.py – Logging setup and miscellaneous helpers.
"""
from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

import config


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """
    Configure root 'p1alert' logger with:
      - Rotating file handler  (DEBUG+)
      - Console StreamHandler  (DEBUG+)

    Safe to call multiple times – handlers are only added once.
    """
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    log_path = Path(log_file) if log_file else Path(config.DEFAULT_LOG_FILE)

    logger = logging.getLogger("p1alert")
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception as exc:
        logger.warning(f"Could not open log file {log_path}: {exc}")

    return logger


def get_logger(name: str = "p1alert") -> logging.Logger:
    return logging.getLogger(name)


def now_iso() -> str:
    return datetime.now().isoformat()


def now_display() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
