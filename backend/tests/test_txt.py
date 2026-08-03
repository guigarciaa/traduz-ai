from app.extractors.txt import TxtDocument


def test_load_splits_on_blank_lines(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("First paragraph.\n\nSecond paragraph.\n\nThird one here.")

    doc = TxtDocument.load(str(path))

    assert doc.units == ["First paragraph.", "Second paragraph.", "Third one here."]


def test_load_ignores_extra_blank_lines_and_whitespace(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("First.\n\n\n\n   \n\nSecond.\n\n")

    doc = TxtDocument.load(str(path))

    assert doc.units == ["First.", "Second."]


def test_load_single_paragraph_no_blank_lines(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Just one paragraph, no breaks at all.")

    doc = TxtDocument.load(str(path))

    assert doc.units == ["Just one paragraph, no breaks at all."]


def test_rebuild_writes_translations_joined_by_blank_line(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("A.\n\nB.")
    doc = TxtDocument.load(str(src))

    out = tmp_path / "book_pt.txt"
    doc.rebuild(["Tradução A.", "Tradução B."], str(out))

    assert out.read_text(encoding="utf-8") == "Tradução A.\n\nTradução B."


def test_rebuild_handles_accented_characters(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("Hello.")
    doc = TxtDocument.load(str(src))

    out = tmp_path / "book_pt.txt"
    doc.rebuild(["Olá, coração, ação, não."], str(out))

    assert out.read_text(encoding="utf-8") == "Olá, coração, ação, não."


def test_constructor_accepts_raw_text_directly():
    doc = TxtDocument("One.\n\nTwo.")
    assert doc.units == ["One.", "Two."]
    assert doc.raw_text == "One.\n\nTwo."
