import fitz
import pytest

from app.extractors.pdf import (
    PdfDocument,
    _merge_touching_blocks,
    _pick_helvetica_variant,
    _sanitize_for_pdf_font,
    _should_merge,
    _srgb_int_to_rgb,
)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_sanitize_maps_typographic_punctuation():
    text = "bullet: • endash: – emdash: — ellipsis: …"
    result = _sanitize_for_pdf_font(text)
    assert result == "bullet: - endash: - emdash: - ellipsis: ..."


def test_sanitize_maps_smart_quotes():
    text = "‘single’ and “double”"
    assert _sanitize_for_pdf_font(text) == "'single' and \"double\""


def test_sanitize_maps_math_and_greek_symbols():
    text = "Π = Npm−Nc, x × y ≥ z"
    assert _sanitize_for_pdf_font(text) == "Pi = Npm-Nc, x x y >= z"


def test_sanitize_leaves_portuguese_accents_untouched():
    text = "Olá, coração, ação, não, café, ê, ô, ü, ç"
    assert _sanitize_for_pdf_font(text) == text


def test_sanitize_replaces_unmappable_characters_with_asterisk():
    text = "emoji: \U0001f600 here"
    result = _sanitize_for_pdf_font(text)
    assert "\U0001f600" not in result
    assert "*" in result


def test_srgb_int_to_rgb_black():
    assert _srgb_int_to_rgb(0) == (0.0, 0.0, 0.0)


def test_srgb_int_to_rgb_white():
    assert _srgb_int_to_rgb(0xFFFFFF) == (1.0, 1.0, 1.0)


def test_srgb_int_to_rgb_red():
    assert _srgb_int_to_rgb(0xFF0000) == (1.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "fontname, expected",
    [
        ("Helvetica", "helv"),
        ("Arial-Bold", "hebo"),
        ("Times-Italic", "heit"),
        ("Times-BoldOblique", "hebi"),
        ("", "helv"),
        (None, "helv"),
    ],
)
def test_pick_helvetica_variant(fontname, expected):
    assert _pick_helvetica_variant(fontname) == expected


# ---------------------------------------------------------------------------
# Block-fragment merging
# ---------------------------------------------------------------------------


def _blk(x0, y0, x1, y1, text="text"):
    return {"bbox": (x0, y0, x1, y1), "font_size": 10.0, "color_rgb": (0, 0, 0), "fontname": "helv", "text": text}


def test_should_merge_touching_fragments():
    a = _blk(50, 100, 300, 150)
    b = _blk(50, 149.8, 300, 200)  # gap = -0.2, within the merge window
    assert _should_merge(a, b) is True


def test_should_not_merge_real_paragraph_gap():
    a = _blk(50, 100, 300, 150)
    b = _blk(50, 165, 300, 200)  # gap = 15, a genuine paragraph/heading break
    assert _should_merge(a, b) is False


def test_should_not_merge_different_columns():
    a = _blk(50, 100, 200, 150)
    b = _blk(250, 150.2, 400, 200)  # touching vertically, but no horizontal overlap
    assert _should_merge(a, b) is False


def test_should_merge_boundary_values():
    a = _blk(50, 100, 300, 150)
    assert _should_merge(a, _blk(50, 150.5, 300, 200)) is True  # gap == 0.5, inclusive
    assert _should_merge(a, _blk(50, 150.6, 300, 200)) is False  # just past the window
    assert _should_merge(a, _blk(50, 148.0, 300, 200)) is True  # gap == -2.0, inclusive
    assert _should_merge(a, _blk(50, 147.9, 300, 200)) is False  # just past the window


def test_merge_touching_blocks_combines_bbox_and_text():
    blocks = [
        _blk(50, 100, 300, 150, "First fragment."),
        _blk(50, 149.8, 300, 200, "Second fragment."),
        _blk(50, 199.9, 300, 250, "Third fragment."),
    ]
    merged = _merge_touching_blocks(blocks)

    assert len(merged) == 1
    assert merged[0]["bbox"] == (50, 100, 300, 250)
    assert merged[0]["text"] == "First fragment. Second fragment. Third fragment."


def test_merge_touching_blocks_keeps_real_breaks_separate():
    blocks = [
        _blk(50, 100, 300, 150, "Paragraph one."),
        _blk(50, 170, 300, 220, "Paragraph two, after a real gap."),
    ]
    merged = _merge_touching_blocks(blocks)

    assert len(merged) == 2
    assert [b["text"] for b in merged] == ["Paragraph one.", "Paragraph two, after a real gap."]


def test_merge_touching_blocks_handles_out_of_order_input():
    blocks = [
        _blk(50, 149.8, 300, 200, "Second fragment."),
        _blk(50, 100, 300, 150, "First fragment."),
    ]
    merged = _merge_touching_blocks(blocks)
    assert len(merged) == 1
    assert merged[0]["text"] == "First fragment. Second fragment."


def test_merge_touching_blocks_empty_input():
    assert _merge_touching_blocks([]) == []


# ---------------------------------------------------------------------------
# max_y bounds (geometry only, via a bare instance - no real file needed)
# ---------------------------------------------------------------------------


def _bare_document(page_count=1):
    doc = PdfDocument.__new__(PdfDocument)
    doc.doc = fitz.open()
    for _ in range(page_count):
        doc.doc.new_page()
    return doc


def test_max_y_bounded_by_next_text_block_below():
    doc = _bare_document()
    doc._blocks = [
        {"page_index": 0, "bbox": (50, 50, 300, 100)},
        {"page_index": 0, "bbox": (50, 150, 300, 200)},
    ]
    doc._obstacles = {}

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == 150
    assert bounds[1] == doc.doc[0].rect.height - 18


def test_max_y_ignores_block_in_different_column():
    doc = _bare_document()
    doc._blocks = [
        {"page_index": 0, "bbox": (50, 50, 200, 100)},
        {"page_index": 0, "bbox": (250, 60, 400, 110)},  # different column
    ]
    doc._obstacles = {}

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == doc.doc[0].rect.height - 18


def test_max_y_bounded_by_image_obstacle():
    doc = _bare_document()
    doc._blocks = [{"page_index": 0, "bbox": (50, 50, 300, 100)}]
    doc._obstacles = {0: [(50, 120, 300, 400)]}

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == 120


def test_max_y_falls_back_to_page_bottom_when_nothing_below():
    doc = _bare_document()
    doc._blocks = [{"page_index": 0, "bbox": (50, 50, 300, 100)}]
    doc._obstacles = {}

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == doc.doc[0].rect.height - 18


def test_max_y_is_independent_per_page():
    doc = _bare_document(page_count=2)
    doc._blocks = [
        {"page_index": 0, "bbox": (50, 50, 300, 100)},
        {"page_index": 1, "bbox": (50, 50, 300, 100)},
    ]
    doc._obstacles = {1: [(50, 120, 300, 400)]}  # only affects page 1, below the block

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == doc.doc[0].rect.height - 18
    assert bounds[1] == 120


# ---------------------------------------------------------------------------
# Font-size fitting: _measure_spare / _insert_with_shrink
# ---------------------------------------------------------------------------


def _bare_document_with_scratch():
    doc = PdfDocument.__new__(PdfDocument)
    doc._scratch_doc = fitz.open()
    return doc


def test_measure_spare_positive_when_text_comfortably_fits():
    doc = _bare_document_with_scratch()
    spare = doc._measure_spare(300, 200, "Short text.", 10, "helv")
    assert spare > 0


def test_measure_spare_negative_when_text_does_not_fit():
    doc = _bare_document_with_scratch()
    long_text = " ".join(["palavra"] * 500)
    spare = doc._measure_spare(50, 10, long_text, 12, "helv")
    assert spare < 0


def test_measure_spare_cleans_up_scratch_pages():
    doc = _bare_document_with_scratch()
    doc._measure_spare(200, 100, "text", 10, "helv")
    doc._measure_spare(200, 100, "more text", 8, "helv")
    assert len(doc._scratch_doc) == 0


def test_insert_with_shrink_draws_content_exactly_once():
    doc = _bare_document_with_scratch()
    real_doc = fitz.open()
    page = real_doc.new_page()
    rect = fitz.Rect(50, 50, 300, 100)

    doc._insert_with_shrink(page, rect, "Texto de teste unico", 10, "helv", (0, 0, 0), max_y=400)

    assert page.get_text().count("Texto de teste unico") == 1


def test_insert_with_shrink_never_loses_text_in_a_tiny_box():
    doc = _bare_document_with_scratch()
    real_doc = fitz.open()
    page = real_doc.new_page()
    rect = fitz.Rect(50, 50, 60, 55)  # far too small for the text below
    long_text = "Este texto é claramente longo demais para a caixa original que foi fornecida aqui."

    doc._insert_with_shrink(page, rect, long_text, 10, "helv", (0, 0, 0), max_y=500)

    assert page.get_text().strip() != ""


def test_insert_with_shrink_never_draws_past_max_y():
    doc = _bare_document_with_scratch()
    real_doc = fitz.open()
    page = real_doc.new_page()
    rect = fitz.Rect(50, 50, 300, 60)  # short box, text will need to grow
    text = " ".join(["palavra"] * 40)
    max_y = 150

    doc._insert_with_shrink(page, rect, text, 10, "helv", (0, 0, 0), max_y=max_y)

    blocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 0]
    assert len(blocks) == 1
    assert blocks[0]["bbox"][3] <= max_y + 0.5  # small tolerance for font metrics


def test_insert_with_shrink_shrinks_font_for_longer_translation():
    doc = _bare_document_with_scratch()
    real_doc = fitz.open()
    page = real_doc.new_page()
    rect = fitz.Rect(50, 50, 250, 66)  # sized for one ~11pt line
    long_translation = (
        "Esta tradução em português é propositalmente bem mais longa que o "
        "espaço original em inglês, para forçar a redução do tamanho da fonte."
    )

    doc._insert_with_shrink(page, rect, long_translation, 11, "helv", (0, 0, 0), max_y=66)

    spans = [
        s
        for b in page.get_text("dict")["blocks"]
        if b["type"] == 0
        for l in b["lines"]
        for s in l["spans"]
    ]
    assert spans
    assert all(s["size"] < 11 for s in spans)


# ---------------------------------------------------------------------------
# Full extraction + rebuild, end to end
# ---------------------------------------------------------------------------


def test_load_extracts_text_blocks_in_reading_order(simple_pdf_path):
    doc = PdfDocument.load(simple_pdf_path)

    assert doc.units == [
        "Hello world, this is a test paragraph.",
        "This is a second short paragraph.",
    ]


def test_rebuild_produces_translated_pdf(simple_pdf_path, tmp_path):
    doc = PdfDocument.load(simple_pdf_path)
    translations = [
        "Olá mundo, este é um parágrafo de teste bem mais longo em português, "
        "propositalmente escrito para forçar a redução do tamanho da fonte.",
        "Este é um segundo parágrafo curto.",
    ]
    out_path = tmp_path / "translated.pdf"

    doc.rebuild(translations, str(out_path))

    assert out_path.exists()
    result = fitz.open(str(out_path))
    text = " ".join(result[0].get_text().split())  # normalize line-wrap whitespace
    assert "Olá mundo" in text
    assert "segundo parágrafo curto" in text
    assert "Hello world" not in text  # original English text was redacted


def test_rebuild_closes_scratch_document(simple_pdf_path, tmp_path):
    doc = PdfDocument.load(simple_pdf_path)
    doc.rebuild(["A.", "B."], str(tmp_path / "out.pdf"))
    assert doc._scratch_doc.is_closed


def test_rebuild_sanitizes_typographic_characters_in_output(simple_pdf_path, tmp_path):
    doc = PdfDocument.load(simple_pdf_path)
    translations = ['Texto com “aspas curvas” e travessão – aqui.', "Segundo parágrafo."]
    out_path = tmp_path / "out.pdf"

    doc.rebuild(translations, str(out_path))

    text = fitz.open(str(out_path))[0].get_text()
    assert "aspas curvas" in text
    assert "“" not in text and "”" not in text


def test_load_and_rebuild_preserve_image_obstacles(tmp_path):
    src = fitz.open()
    page = src.new_page()
    page.insert_text((72, 100), "Caption text here", fontsize=10)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    pix.set_rect(pix.irect, (255, 0, 0))
    page.insert_image(fitz.Rect(72, 120, 200, 220), pixmap=pix)
    page.insert_text((72, 240), "Text after image", fontsize=10)
    src_path = tmp_path / "with_image.pdf"
    src.save(str(src_path))
    src.close()

    doc = PdfDocument.load(str(src_path))

    assert 0 in doc._obstacles
    assert len(doc._obstacles[0]) == 1
    assert doc._obstacles[0][0][1] == pytest.approx(120, abs=1)

    caption_index = doc.units.index("Caption text here")
    bounds = doc._compute_max_y_bounds()
    assert bounds[caption_index] <= 120.5

    out_path = tmp_path / "out.pdf"
    doc.rebuild(["Texto da legenda aqui", "Texto depois da imagem"], str(out_path))

    result = fitz.open(str(out_path))
    # the image itself must still be present as a raster image on the page
    assert len(result[0].get_images()) == 1


def test_load_classmethod_matches_constructor(simple_pdf_path):
    via_load = PdfDocument.load(simple_pdf_path)
    via_ctor = PdfDocument(simple_pdf_path)
    assert via_load.units == via_ctor.units


def test_load_skips_whitespace_only_text_blocks(tmp_path):
    src = fitz.open()
    page = src.new_page()
    page.insert_text((72, 100), " ", fontsize=12)  # whitespace-only block
    page.insert_text((72, 140), "Real text", fontsize=12)
    path = tmp_path / "whitespace.pdf"
    src.save(str(path))
    src.close()

    doc = PdfDocument.load(str(path))

    assert doc.units == ["Real text"]


def test_max_y_ignores_obstacle_in_different_column():
    doc = _bare_document()
    doc._blocks = [{"page_index": 0, "bbox": (50, 50, 200, 100)}]
    # obstacle is below vertically, but in a completely different column
    doc._obstacles = {0: [(300, 120, 450, 200)]}

    bounds = doc._compute_max_y_bounds()

    assert bounds[0] == doc.doc[0].rect.height - 18
