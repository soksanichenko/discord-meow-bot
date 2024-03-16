"""Helpful utilities"""

import logging


class Logger(logging.Logger):
    """Logger for the bot"""

    __instance__ = None

    def __new__(
        cls,
        name: str = 'discord',
        level: int = logging.INFO,
    ):
        if cls.__instance__ is None:
            cls.__instance__ = logging.getLogger(name=name)
            cls.__instance__.setLevel(level=level)
        return cls.__instance__
