"""Parser for .siq (KvizGame) package files.

A .siq file is a ZIP archive containing content.xml and media folders:
  Images/, Audio/, Video/

content.xml hierarchy:
  package → rounds/round → themes/theme → questions/question
  question → scenario/atom, right/answer, wrong/answer
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# Maps atom type to the archive folder holding its media files.
# 'voice' is the v4 name; 'audio' is used in v5 packs.
_MEDIA_FOLDERS: dict[str, str] = {
    'image': 'Images',
    'voice': 'Audio',
    'audio': 'Audio',
    'video': 'Video',
}


@dataclass
class Atom:
    """A single content element within a question scenario.

    Attributes:
        type: One of 'text', 'say', 'image', 'voice', 'video', 'marker'.
        content: Displayed text, or a filename reference for media atoms.
            Media filenames may carry a leading '@' prefix in some packs.
        time: Display duration hint in seconds (0 = no limit).
    """

    type: str
    content: str = ''
    time: int = 0

    @property
    def is_media(self) -> bool:
        """Return True if this atom refers to a file in the archive."""
        return self.type in _MEDIA_FOLDERS

    @property
    def media_path(self) -> str | None:
        """Archive-relative path to the media file, or None for text atoms."""
        if not self.is_media:
            return None
        filename = self.content.lstrip('@')
        return f'{_MEDIA_FOLDERS[self.type]}/{filename}'


@dataclass
class Question:
    """A single question with its scenario, type, and answers.

    Attributes:
        price: Point value (may be negative for some question types).
        q_type: Question type identifier ('simple', 'auction', 'cat', etc.).
        type_params: Extra parameters for non-simple types (e.g. cat theme/price).
        scenario: Ordered list of content atoms shown to players.
        right: Accepted correct answers.
        wrong: Explicitly wrong answers (rarely used).
    """

    price: int
    q_type: str = 'simple'
    type_params: dict[str, str] = field(default_factory=dict)
    scenario: list[Atom] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)


@dataclass
class Theme:
    """A category (column) on the game board.

    Attributes:
        name: Category name shown in the board header.
        questions: Questions ordered by price (ascending).
    """

    name: str
    questions: list[Question] = field(default_factory=list)


@dataclass
class Round:
    """A game round containing one or more themes.

    Attributes:
        name: Round title.
        is_final: True for the final round (different rules apply).
        themes: Categories in this round.
    """

    name: str
    is_final: bool = False
    themes: list[Theme] = field(default_factory=list)


@dataclass
class PackageInfo:
    """Authorship metadata attached to a package.

    Attributes:
        authors: List of author names.
        sources: List of source references.
        comments: Free-form comment text.
    """

    authors: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    comments: str = ''


@dataclass
class Package:
    """Top-level object representing a parsed .siq pack.

    Attributes:
        name: Pack title.
        rounds: Game rounds in order.
        info: Authorship metadata.
        difficulty: Difficulty rating 1–10 (default 5).
        language: BCP-47 language tag, or empty string.
    """

    name: str
    rounds: list[Round] = field(default_factory=list)
    info: PackageInfo = field(default_factory=PackageInfo)
    difficulty: int = 5
    language: str = ''


class SiqPackage:
    """A loaded .siq archive with access to its content and media files.

    Args:
        package: Parsed package structure.
        path: Path to the source .siq archive (needed for media extraction).
    """

    def __init__(self, package: Package, path: str | Path) -> None:
        self._package = package
        self._path = Path(path)

    @property
    def package(self) -> Package:
        """The parsed package structure."""
        return self._package

    def read_media(self, atom: Atom) -> bytes:
        """Return raw bytes for a media atom's file.

        Args:
            atom: A media atom (atom.is_media must be True).

        Returns:
            Raw file contents from the archive.

        Raises:
            ValueError: If the atom does not reference a media file.
            KeyError: If the file is missing from the archive.
        """
        if not atom.is_media:
            raise ValueError(f'Atom type {atom.type!r} has no associated media file')
        with zipfile.ZipFile(self._path) as zf:
            return zf.read(atom.media_path)

    def list_media(self) -> list[str]:
        """Return archive-relative paths of all media files in the package.

        Returns:
            Sorted list of paths like 'Images/foo.png', 'Audio/bar.mp3'.
        """
        with zipfile.ZipFile(self._path) as zf:
            folders = set(_MEDIA_FOLDERS.values())
            return sorted(
                name
                for name in zf.namelist()
                if name.split('/')[0] in folders and not name.endswith('/')
            )


def load(path: str | Path) -> SiqPackage:
    """Parse a .siq file and return a SiqPackage.

    Args:
        path: Path to the .siq archive.

    Returns:
        SiqPackage with parsed structure and media access.

    Raises:
        zipfile.BadZipFile: If the file is not a valid ZIP archive.
        KeyError: If content.xml is missing from the archive.
        ET.ParseError: If content.xml is malformed.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        with zf.open('content.xml') as fp:
            root = ET.parse(fp).getroot()
    _strip_namespace(root)
    package = _parse_package(root)
    return SiqPackage(package, path)


# ---------------------------------------------------------------------------
# Internal XML parsing helpers
# ---------------------------------------------------------------------------


def _strip_namespace(el: ET.Element) -> None:
    """Remove XML namespace prefixes from all tags in-place.

    ElementTree preserves Clark-notation namespaces ({uri}tag) which
    breaks plain-text XPath queries. This strips them so findall works
    regardless of whether the file declares xmlns=.
    """
    for node in el.iter():
        if '}' in node.tag:
            node.tag = node.tag.split('}', 1)[1]


def _parse_package(el: ET.Element) -> Package:
    difficulty_str = el.get('difficulty', '5')
    return Package(
        name=el.get('name', ''),
        difficulty=int(difficulty_str) if difficulty_str.isdigit() else 5,
        language=el.get('language', ''),
        info=_parse_info(el),
        rounds=[_parse_round(r) for r in el.findall('./rounds/round')],
    )


def _parse_info(el: ET.Element) -> PackageInfo:
    info = PackageInfo()
    el_info = el.find('info')
    if el_info is None:
        return info
    info.authors = [a.text or '' for a in el_info.findall('./authors/author')]
    info.sources = [s.text or '' for s in el_info.findall('./sources/source')]
    el_comments = el_info.find('comments')
    if el_comments is not None:
        info.comments = el_comments.text or ''
    return info


def _parse_round(el: ET.Element) -> Round:
    return Round(
        name=el.get('name', ''),
        is_final=el.get('type') == 'final',
        themes=[_parse_theme(t) for t in el.findall('./themes/theme')],
    )


def _parse_theme(el: ET.Element) -> Theme:
    return Theme(
        name=el.get('name', ''),
        questions=[_parse_question(q) for q in el.findall('./questions/question')],
    )


def _parse_question(el: ET.Element) -> Question:
    price_str = el.get('price', '0')
    question = Question(
        price=int(price_str) if price_str.lstrip('-').isdigit() else 0,
    )

    # v4: <type name="auction"> / v5: no separate type element (type in <params>)
    el_type = el.find('type')
    if el_type is not None:
        question.q_type = el_type.get('name', 'simple')
        question.type_params = {
            p.get('name', ''): (p.text or '') for p in el_type.findall('param')
        }

    # v4: <scenario><atom>  /  v5: <params><param name="question"><item>
    if el.find('scenario') is not None:
        question.scenario = [_parse_atom(a) for a in el.findall('./scenario/atom')]
    else:
        question.scenario = [
            _parse_item(i) for i in el.findall('./params/param[@name="question"]/item')
        ]

    question.right = [a.text or '' for a in el.findall('./right/answer')]
    question.wrong = [a.text or '' for a in el.findall('./wrong/answer')]
    return question


def _parse_atom(el: ET.Element) -> Atom:
    """Parse a v4 <atom> element."""
    time_str = el.get('time', '0')
    return Atom(
        type=el.get('type', 'text'),
        content=el.text or '',
        time=int(time_str) if time_str.isdigit() else 0,
    )


def _parse_item(el: ET.Element) -> Atom:
    """Parse a v5 <item> element inside <param name="question">."""
    item_type = el.get('type', 'text')
    # v5 uses 'text' for plain items (no type attr), same media types as v4
    time_str = el.get('duration', '0')
    return Atom(
        type=item_type,
        content=el.text or '',
        time=int(time_str) if time_str.isdigit() else 0,
    )
