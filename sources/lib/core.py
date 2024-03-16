"""Core functionality for the bot"""

import base64
from pathlib import Path


class BotAvatar:
    """Class-singleton for saving of an avatar of the bot"""

    __avatar__ = None

    def __new__(cls, *args, **kwargs):  # pylint: disable=unused-argument
        if cls.__avatar__ is None:
            cls.__avatar__ = cls.load_bot_avatar()
        return cls.__avatar__

    @staticmethod
    def load_bot_avatar() -> bytes:
        """
        Load a bot's avatar
        :return: bytes of a bot's avatar
        """
        path = Path(__file__).parent.joinpath('../static/avatar.gif.base64')
        with path.open(mode='r', encoding='utf-8') as fd:
            data = base64.b64decode(fd.read())
        return data
