import ebooklib
from ebooklib import epub

from app.extractors.epub import EpubDocument, _find_translatable_blocks
from bs4 import BeautifulSoup


def test_extracts_paragraphs_headings_and_list_items(make_epub):
    path = make_epub()
    doc = EpubDocument.load(path)

    assert "Chapter One" in doc.units
    assert "This is the first paragraph of the book." in doc.units
    assert "First item" in doc.units
    assert "Second item" in doc.units


def test_inline_formatting_is_flattened_to_plain_text(make_epub):
    path = make_epub()
    doc = EpubDocument.load(path)

    # <p>Here is a <b>second</b> paragraph with some <i>formatting</i> inside it.</p>
    assert "Here is a second paragraph with some formatting inside it." in doc.units


def test_table_cell_is_translated_but_table_wrapper_is_not_a_unit(make_epub):
    path = make_epub()
    doc = EpubDocument.load(path)

    # <td> itself is a BLOCK_TAG and doesn't contain img/svg/table, so its
    # own text is translatable...
    assert "cell" in doc.units
    # ...but nothing wraps the whole <table> as a single flattened unit that
    # would destroy its structure.
    assert not any("cell" in u and u != "cell" for u in doc.units)


def test_nav_document_is_never_extracted(make_epub):
    path = make_epub()
    doc = EpubDocument.load(path)

    # ebooklib's generated nav.xhtml has a "<h2>Test Book</h2>" heading and a
    # "<li><a>Chapter 1</a></li>" toc entry - neither should leak in.
    assert "Test Book" not in doc.units
    assert "Chapter 1" not in doc.units


def test_block_nested_in_another_block_is_not_duplicated(make_epub):
    # <li><p>nested</p></li>: only the outer <li> should become a unit,
    # since the inner <p> has a BLOCK_TAGS ancestor.
    path = make_epub(extra_body="<ul><li><p>nested paragraph in a list item</p></li></ul>")
    doc = EpubDocument.load(path)

    matches = [u for u in doc.units if "nested paragraph in a list item" in u]
    assert len(matches) == 1


def test_block_containing_image_is_skipped(make_epub):
    path = make_epub(extra_body='<p>Caption text <img src="x.jpg"/> more text</p>')
    doc = EpubDocument.load(path)

    assert not any("Caption text" in u for u in doc.units)


def test_empty_block_is_skipped(make_epub):
    path = make_epub(extra_body="<p>   </p><p>\n\n</p>")
    doc = EpubDocument.load(path)

    assert "" not in doc.units
    assert all(u.strip() for u in doc.units)


def test_rebuild_replaces_text_and_preserves_structure(make_epub, tmp_path):
    path = make_epub()
    doc = EpubDocument.load(path)

    translations = [f"[PT] {u}" for u in doc.units]
    out_path = tmp_path / "translated.epub"
    doc.rebuild(translations, str(out_path))

    assert out_path.exists()

    rebuilt = epub.read_epub(str(out_path))
    chapter = next(
        item
        for item in rebuilt.get_items_of_type(ebooklib.ITEM_DOCUMENT)
        if item.get_name() == "chap1.xhtml"
    )
    soup = BeautifulSoup(chapter.get_content(), "html.parser")

    assert soup.find("h1").get_text() == "[PT] Chapter One"
    assert soup.find("img") is not None  # image untouched
    assert soup.find("img")["src"] == "cover.jpg"


def test_rebuild_leaves_nav_document_untouched(make_epub, tmp_path):
    path = make_epub()
    doc = EpubDocument.load(path)
    translations = [f"[PT] {u}" for u in doc.units]

    out_path = tmp_path / "translated.epub"
    doc.rebuild(translations, str(out_path))

    rebuilt = epub.read_epub(str(out_path))
    nav = next(
        item
        for item in rebuilt.get_items_of_type(ebooklib.ITEM_DOCUMENT)
        if item.get_name() == "nav.xhtml"
    )
    soup = BeautifulSoup(nav.get_content(), "html.parser")
    link = soup.find("a")
    assert link is not None
    assert link["href"] == "chap1.xhtml"
    assert link.get_text() == "Chapter 1"


def test_find_translatable_blocks_directly_on_soup():
    soup = BeautifulSoup("<body><p>Alpha</p><div><p>Beta</p></div></body>", "html.parser")
    blocks = _find_translatable_blocks(soup)
    texts = [b.get_text() for b in blocks]
    assert texts == ["Alpha", "Beta"]
