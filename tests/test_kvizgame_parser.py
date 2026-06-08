"""Tests for sources/lib/kvizgame/parser.py."""

import zipfile

import pytest

from sources.lib.kvizgame.parser import (
    load,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_siq(
    content_xml: str, media: dict[str, bytes] | None = None, tmp_path=None
) -> str:
    """Write an in-memory .siq archive to a temp file and return its path."""
    path = tmp_path / 'test.siq'
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('content.xml', content_xml)
        for name, data in (media or {}).items():
            zf.writestr(name, data)
    return str(path)


MINIMAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<package name="Test Pack" version="4" difficulty="3" language="ru">
  <rounds/>
</package>
"""

FULL_XML = """<?xml version="1.0" encoding="utf-8"?>
<package name="Full Pack" version="4" difficulty="7">
  <info>
    <authors><author>Alice</author><author>Bob</author></authors>
    <sources><source>Wikipedia</source></sources>
    <comments>A test pack.</comments>
  </info>
  <rounds>
    <round name="Round 1">
      <themes>
        <theme name="Science">
          <questions>
            <question price="100">
              <scenario>
                <atom>What is H2O?</atom>
              </scenario>
              <right><answer>Water</answer></right>
            </question>
            <question price="200">
              <scenario>
                <atom>What planet is closest to the Sun?</atom>
              </scenario>
              <right><answer>Mercury</answer></right>
              <wrong><answer>Venus</answer></wrong>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
    <round name="Final" type="final">
      <themes>
        <theme name="Mixed">
          <questions>
            <question price="0">
              <scenario><atom>Ultimate question</atom></scenario>
              <right><answer>42</answer></right>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
  </rounds>
</package>
"""

AUCTION_XML = """<?xml version="1.0" encoding="utf-8"?>
<package name="P" version="4">
  <rounds>
    <round name="R1">
      <themes>
        <theme name="T1">
          <questions>
            <question price="500">
              <type name="auction"/>
              <scenario><atom>Bid question</atom></scenario>
              <right><answer>Right</answer></right>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
  </rounds>
</package>
"""

CAT_XML = """<?xml version="1.0" encoding="utf-8"?>
<package name="P" version="4">
  <rounds>
    <round name="R1">
      <themes>
        <theme name="T1">
          <questions>
            <question price="300">
              <type name="cat">
                <param name="theme">Secret Theme</param>
                <param name="cost">1000</param>
              </type>
              <scenario><atom>Cat question</atom></scenario>
              <right><answer>Answer</answer></right>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
  </rounds>
</package>
"""

MEDIA_XML = """<?xml version="1.0" encoding="utf-8"?>
<package name="P" version="4">
  <rounds>
    <round name="R1">
      <themes>
        <theme name="T1">
          <questions>
            <question price="100">
              <scenario>
                <atom type="image">@photo.png</atom>
                <atom type="voice">sound.mp3</atom>
                <atom>Text clue</atom>
              </scenario>
              <right><answer>Right</answer></right>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
  </rounds>
</package>
"""


# ---------------------------------------------------------------------------
# Package-level parsing
# ---------------------------------------------------------------------------


class TestParsePackage:
    def test_name_and_metadata(self, tmp_path):
        pkg = load(_make_siq(MINIMAL_XML, tmp_path=tmp_path)).package
        assert pkg.name == 'Test Pack'
        assert pkg.difficulty == 3
        assert pkg.language == 'ru'

    def test_info_authors_and_sources(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        assert pkg.info.authors == ['Alice', 'Bob']
        assert pkg.info.sources == ['Wikipedia']
        assert pkg.info.comments == 'A test pack.'

    def test_no_info_returns_empty(self, tmp_path):
        pkg = load(_make_siq(MINIMAL_XML, tmp_path=tmp_path)).package
        assert pkg.info.authors == []
        assert pkg.info.comments == ''


# ---------------------------------------------------------------------------
# Round parsing
# ---------------------------------------------------------------------------


class TestParseRound:
    def test_round_count_and_names(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        assert len(pkg.rounds) == 2
        assert pkg.rounds[0].name == 'Round 1'
        assert pkg.rounds[1].name == 'Final'

    def test_final_round_flag(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        assert not pkg.rounds[0].is_final
        assert pkg.rounds[1].is_final

    def test_empty_rounds(self, tmp_path):
        pkg = load(_make_siq(MINIMAL_XML, tmp_path=tmp_path)).package
        assert pkg.rounds == []


# ---------------------------------------------------------------------------
# Theme and question parsing
# ---------------------------------------------------------------------------


class TestParseThemeAndQuestion:
    def test_theme_name_and_question_count(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        theme = pkg.rounds[0].themes[0]
        assert theme.name == 'Science'
        assert len(theme.questions) == 2

    def test_question_price(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        questions = pkg.rounds[0].themes[0].questions
        assert questions[0].price == 100
        assert questions[1].price == 200

    def test_right_and_wrong_answers(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        q = pkg.rounds[0].themes[0].questions[1]
        assert q.right == ['Mercury']
        assert q.wrong == ['Venus']

    def test_simple_type_is_default(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        assert pkg.rounds[0].themes[0].questions[0].q_type == 'simple'

    def test_auction_type(self, tmp_path):
        pkg = load(_make_siq(AUCTION_XML, tmp_path=tmp_path)).package
        q = pkg.rounds[0].themes[0].questions[0]
        assert q.q_type == 'auction'
        assert q.type_params == {}

    def test_cat_type_with_params(self, tmp_path):
        pkg = load(_make_siq(CAT_XML, tmp_path=tmp_path)).package
        q = pkg.rounds[0].themes[0].questions[0]
        assert q.q_type == 'cat'
        assert q.type_params == {'theme': 'Secret Theme', 'cost': '1000'}


# ---------------------------------------------------------------------------
# Atom parsing
# ---------------------------------------------------------------------------


class TestParseAtom:
    def test_text_atom(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        atom = pkg.rounds[0].themes[0].questions[0].scenario[0]
        assert atom.type == 'text'
        assert atom.content == 'What is H2O?'
        assert not atom.is_media

    def test_image_atom_is_media(self, tmp_path):
        pkg = load(_make_siq(MEDIA_XML, tmp_path=tmp_path)).package
        atom = pkg.rounds[0].themes[0].questions[0].scenario[0]
        assert atom.type == 'image'
        assert atom.is_media

    def test_image_media_path_strips_at_prefix(self, tmp_path):
        pkg = load(_make_siq(MEDIA_XML, tmp_path=tmp_path)).package
        atom = pkg.rounds[0].themes[0].questions[0].scenario[0]
        assert atom.media_path == 'Images/photo.png'

    def test_voice_media_path_without_at_prefix(self, tmp_path):
        pkg = load(_make_siq(MEDIA_XML, tmp_path=tmp_path)).package
        atom = pkg.rounds[0].themes[0].questions[0].scenario[1]
        assert atom.type == 'voice'
        assert atom.media_path == 'Audio/sound.mp3'

    def test_text_atom_media_path_is_none(self, tmp_path):
        pkg = load(_make_siq(FULL_XML, tmp_path=tmp_path)).package
        atom = pkg.rounds[0].themes[0].questions[0].scenario[0]
        assert atom.media_path is None


# ---------------------------------------------------------------------------
# SiqPackage media access
# ---------------------------------------------------------------------------


class TestSiqPackageMedia:
    def test_read_media_returns_bytes(self, tmp_path):
        image_data = b'\x89PNG\r\n\x1a\n'
        siq = load(
            _make_siq(
                MEDIA_XML, media={'Images/photo.png': image_data}, tmp_path=tmp_path
            )
        )
        atom = siq.package.rounds[0].themes[0].questions[0].scenario[0]
        assert siq.read_media(atom) == image_data

    def test_read_media_raises_for_text_atom(self, tmp_path):
        siq = load(_make_siq(FULL_XML, tmp_path=tmp_path))
        atom = siq.package.rounds[0].themes[0].questions[0].scenario[0]
        with pytest.raises(ValueError, match='no associated media file'):
            siq.read_media(atom)

    def test_list_media_returns_sorted_paths(self, tmp_path):
        media = {
            'Images/photo.png': b'img',
            'Audio/sound.mp3': b'aud',
            'Images/': b'',  # directory entry — should be excluded
        }
        siq = load(_make_siq(MEDIA_XML, media=media, tmp_path=tmp_path))
        assert siq.list_media() == ['Audio/sound.mp3', 'Images/photo.png']

    def test_list_media_empty_when_no_media(self, tmp_path):
        siq = load(_make_siq(FULL_XML, tmp_path=tmp_path))
        assert siq.list_media() == []
