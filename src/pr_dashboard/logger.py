"""Logging setup for PR Dashboard — rotating file + in-app ring buffer."""

from __future__ import annotations

import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_dir

LOG_DIR = Path(user_log_dir("pr-dashboard", ensure_exists=True))
LOG_FILE = LOG_DIR / "pr-dashboard.log"

_logger: logging.Logger | None = None
_ring: "RingBufferHandler | None" = None


class RingBufferHandler(logging.Handler):
    """In-memory ring buffer that keeps the last N formatted log records."""

    def __init__(self, capacity: int = 50) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.append(self.format(record))

    def get_messages(self) -> list[str]:
        return list(self.buffer)


def get_logger() -> logging.Logger:
    """Return the singleton pr-dashboard logger."""
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger("pr-dashboard")
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False

    # Rotating file handler — DEBUG+ (2 backup files, 1MB each)
    file_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_fmt)
    _logger.addHandler(fh)

    # Ring buffer handler — INFO+ (for in-app log viewer)
    ring_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
    )
    global _ring
    _ring = RingBufferHandler(capacity=50)
    _ring.setLevel(logging.INFO)
    _ring.setFormatter(ring_fmt)
    _logger.addHandler(_ring)

    return _logger


def get_ring_buffer() -> RingBufferHandler:
    """Return the ring buffer handler (initialises logger if needed)."""
    if _ring is None:
        get_logger()
    assert _ring is not None
    return _ring
