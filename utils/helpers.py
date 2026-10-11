"""
Internal helpers
================
Small, purely-internal utilities used to reduce duplication across
services. None of these change any request/response behavior.
"""

import functools
import logging
import time

import config

logger = logging.getLogger(config.LOGGER_NAME)


def log_duration(label: str):
    """Decorator that logs how long a function took to run (INFO level)."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - start
                logger.info("%s finished in %.2fs", label, elapsed)

        return wrapper

    return decorator
