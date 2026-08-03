import re

import ebooklib
from bs4 import BeautifulSoup, NavigableString
from ebooklib import epub

BLOCK_TAGS = [
    "p",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "dt",
    "dd",
    "blockquote",
    "td",
    "th",
    "caption",
    "figcaption",
]

WHITESPACE_RE = re.compile(r"\s+")


def _find_translatable_blocks(soup: BeautifulSoup) -> list:
    blocks = []
    for el in soup.find_all(BLOCK_TAGS):
        # Skip blocks nested inside another translatable block (e.g. <p> inside <li>)
        if el.find_parent(BLOCK_TAGS):
            continue
        # Skip blocks that contain images/tables - those need structural handling,
        # not plain-text replacement.
        if el.find(["img", "svg", "table"]):
            continue
        text = WHITESPACE_RE.sub(" ", el.get_text(" ", strip=True)).strip()
        if not text:
            continue
        blocks.append(el)
    return blocks


class EpubDocument:
    """EPUB document with translatable text blocks extracted from every
    XHTML content document, keeping structure, CSS and images untouched."""

    def __init__(self, path: str):
        self.path = path
        self.book = epub.read_epub(path)
        self._blocks: list[tuple] = []  # (item, element)
        self._soups: dict[str, tuple] = {}  # item_id -> (item, soup)
        self.units: list[str] = []

        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            # Skip nav/cover documents: their <li><a> links are e-reader
            # navigation, and clearing them to insert plain text would
            # destroy the table of contents.
            if not item.is_chapter():
                continue
            soup = BeautifulSoup(item.get_content(), "html.parser")
            self._soups[item.get_id()] = (item, soup)
            for el in _find_translatable_blocks(soup):
                text = WHITESPACE_RE.sub(" ", el.get_text(" ", strip=True)).strip()
                self._blocks.append((item, el))
                self.units.append(text)

    @classmethod
    def load(cls, path: str) -> "EpubDocument":
        return cls(path)

    def rebuild(self, translations: list[str], output_path: str) -> None:
        for (item, el), translated in zip(self._blocks, translations):
            el.clear()
            el.append(NavigableString(translated))

        for item, soup in self._soups.values():
            item.set_content(str(soup).encode("utf-8"))

        epub.write_epub(output_path, self.book)
