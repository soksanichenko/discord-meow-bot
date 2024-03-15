"""Core functionality for the bot"""
import base64
from pathlib import Path

from sources.lib.utils import singleton


@singleton
class BotAvatar:
    """Class-singleton for saving of an avatar of the bot"""
    __avatar__ = None

    def __init__(self):
        self.__avatar__ = self.load_bot_avatar()

    @property
    def avatar(self):
        """Get an avatar of the bot like as bytes-object"""
        return self.__avatar__

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
