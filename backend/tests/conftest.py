import ebooklib
import fitz
import pytest
from ebooklib import epub

from app import db


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file - never the real
    project DB. Autouse + session-wide (conftest.py) since db.save_job() is
    called from jobs.create_job(), which many tests touch indirectly."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


@pytest.fixture
def make_epub(tmp_path):
    """Builds a minimal, structurally-real EPUB and returns its path.

    Includes a chapter with paragraphs/headings/list/image (so we exercise
    the "skip blocks containing img" rule) and lets ebooklib generate its own
    nav.xhtml (so we exercise the is_chapter() filter against a real
    generated nav document, not a hand-rolled one).
    """

    def _make(extra_body: str = "") -> str:
        book = epub.EpubBook()
        book.set_identifier("test-id-123")
        book.set_title("Test Book")
        book.set_language("en")
        book.add_author("Test Author")

        chapter = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
        chapter.content = f"""<html><body>
<h1>Chapter One</h1>
<p>This is the first paragraph of the book.</p>
<p>Here is a <b>second</b> paragraph with some <i>formatting</i> inside it.</p>
<img src="cover.jpg" alt="cover"/>
<ul><li>First item</li><li>Second item</li></ul>
<table><tr><td>cell</td></tr></table>
{extra_body}
</body></html>"""
        book.add_item(chapter)
        book.toc = (epub.Link("chap1.xhtml", "Chapter 1", "chap1"),)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", chapter]

        out_path = tmp_path / "test_book.epub"
        epub.write_epub(str(out_path), book)
        return str(out_path)

    return _make


@pytest.fixture
def make_pdf(tmp_path):
    """Builds a PDF with fine-grained control over text block placement, for
    testing the merge / max_y-bound / shrink-to-fit logic in pdf.py without
    depending on any particular real-world book file."""

    def _make(name: str = "test.pdf") -> tuple:
        doc = fitz.open()
        page = doc.new_page()
        out_path = tmp_path / name
        return doc, page, str(out_path)

    return _make


@pytest.fixture
def simple_pdf_path(tmp_path):
    """A basic single-page PDF: two short paragraphs plus a filled rectangle
    (a non-text/"image-like" element) below them."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Hello world, this is a test paragraph.", fontsize=12)
    page.insert_text((72, 140), "This is a second short paragraph.", fontsize=12)
    page.draw_rect(fitz.Rect(72, 200, 300, 350), color=(0, 0, 1), fill=(0.8, 0.8, 1))
    out_path = tmp_path / "simple.pdf"
    doc.save(str(out_path))
    doc.close()
    return str(out_path)
