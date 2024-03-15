"""Helpful utilities"""

import logging


def singleton(class_):
    """Singleton decorator"""
    instances = {}

    def getinstance(*args, **kwargs):
        if class_ not in instances:
            instances[class_] = class_(*args, **kwargs)
        return instances[class_]

    return getinstance


class Logger:
    # pylint: disable=too-few-public-methods
    """Logging class"""

    __logger__ = None

    def __init__(self, level: int = logging.INFO):
        self.__logger__ = logging.getLogger('discord')
        self.__logger__.setLevel(level)

    @property
    def logger(self) -> logging.Logger:
        """Get initialized logger"""
        return self.__logger__
