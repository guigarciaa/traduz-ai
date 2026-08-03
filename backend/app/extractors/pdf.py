import re
from collections import defaultdict

import fitz  # PyMuPDF

WHITESPACE_RE = re.compile(r"\s+")
MIN_FONTSIZE = 5.0
FONTSIZE_STEP = 0.5

# PyMuPDF's base-14 fonts ("helv" etc.) encode text as Latin-1 (ISO-8859-1),
# which has no glyphs in the 0x80-0x9F range - exactly where "smart" typographic
# punctuation (curly quotes, en/em dash, bullet, ellipsis) lives in Unicode.
# Without this, those characters silently render as "?". Portuguese accented
# letters are unaffected since they *are* in Latin-1.
_TYPOGRAPHIC_CHAR_MAP = str.maketrans(
    {
        "‘": "'",  # left single quote
        "’": "'",  # right single quote
        "‚": ",",  # single low-9 quote
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "„": '"',  # double low-9 quote
        "–": "-",  # en dash
        "—": "-",  # em dash
        "•": "-",  # bullet
        "…": "...",  # ellipsis
        " ": " ",  # non-breaking space
        "−": "-",  # minus sign (U+2212, distinct from hyphen)
        "×": "x",  # multiplication sign
        "÷": "/",  # division sign
        "≥": ">=",
        "≤": "<=",
        "≠": "!=",
        "±": "+/-",
        "→": "->",
        "←": "<-",
        "∞": "infinito",
        "√": "raiz de ",
        "Π": "Pi",  # capital pi (used as a variable, e.g. profit formulas)
        "π": "pi",
        "Σ": "Soma",
        "™": "(TM)",
    }
)


def _sanitize_for_pdf_font(text: str) -> str:
    sanitized = text.translate(_TYPOGRAPHIC_CHAR_MAP)
    # Catch-all: any remaining character the Latin-1 base-14 font can't
    # encode would otherwise silently render as "?" with no way to tell
    # apart from a real question mark - replace with a visibly different
    # placeholder so it is at least obvious something did not map cleanly.
    return "".join(c if ord(c) < 256 else "*" for c in sanitized)


def _srgb_int_to_rgb(color_int: int) -> tuple:
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return (r / 255, g / 255, b / 255)


def _pick_helvetica_variant(fontname_sample: str) -> str:
    fn = (fontname_sample or "").lower()
    bold = "bold" in fn
    italic = "italic" in fn or "oblique" in fn
    if bold and italic:
        return "hebi"
    if bold:
        return "hebo"
    if italic:
        return "heit"
    return "helv"


# PyMuPDF sometimes splits a single visual paragraph into several consecutive
# "blocks" (e.g. around an inline reference like "Fig. 1.1"), with virtually
# no gap between them (~-0.2pt in this book, vs. 0.5pt+ for genuine paragraph/
# heading breaks - confirmed by inspecting the gap distribution across the
# whole document). Translating and reinserting such fragments independently
# leaves each one with zero slack to grow into if the translation is longer,
# causing it to overlap the next fragment. Merging touching fragments first
# gives the shrink-to-fit logic the whole paragraph's original height to work
# with, which is what actually fixes the overlap.
_MERGE_GAP_MIN = -2.0
_MERGE_GAP_MAX = 0.5


def _should_merge(a: dict, b: dict) -> bool:
    ax0, ay0, ax1, ay1 = a["bbox"]
    bx0, by0, bx1, by1 = b["bbox"]
    horizontal_overlap = min(ax1, bx1) - max(ax0, bx0)
    min_width = min(ax1 - ax0, bx1 - bx0)
    if min_width <= 0 or horizontal_overlap / min_width < 0.5:
        return False  # not the same column
    gap = by0 - ay1
    return _MERGE_GAP_MIN <= gap <= _MERGE_GAP_MAX


def _merge_touching_blocks(blocks: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for blk in sorted(blocks, key=lambda b: b["bbox"][1]):
        if merged and _should_merge(merged[-1], blk):
            prev = merged[-1]
            x0, y0, x1, y1 = prev["bbox"]
            bx0, by0, bx1, by1 = blk["bbox"]
            prev["bbox"] = (min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1))
            prev["text"] = f"{prev['text']} {blk['text']}".strip()
            prev["font_size"] = (prev["font_size"] + blk["font_size"]) / 2
        else:
            merged.append(dict(blk))
    return merged


class PdfDocument:
    """PDF document with per-page text blocks extracted for translation.

    Images and vector graphics are left untouched; only text block regions
    are redacted and replaced with translated text in the same bounding box,
    shrinking the font as needed to fit.
    """

    def __init__(self, path: str):
        self.path = path
        self.doc = fitz.open(path)
        self._blocks: list[dict] = []
        self.units: list[str] = []
        # insert_textbox draws immediately and cumulatively: calling it twice
        # on the same rect (e.g. while trying progressively smaller
        # fontsizes) leaves BOTH renders on the page, overlapping each other
        # - confirmed empirically. So font-size trials are measured on a
        # disposable scratch page, and the real page only ever receives one
        # single, final insert_textbox call per block.
        self._scratch_doc = fitz.open()
        # Non-text regions (images, figures) per page, kept so translated
        # text never grows down into them - only text blocks are candidates
        # for translation, but images are still obstacles to avoid.
        self._obstacles: dict[int, list[tuple]] = defaultdict(list)

        for page_index, page in enumerate(self.doc):
            page_dict = page.get_text("dict")
            raw_blocks = []
            for block in page_dict["blocks"]:
                if block.get("type") != 0:
                    self._obstacles[page_index].append(tuple(block["bbox"]))
                    continue  # not a text block (e.g. image)

                text_parts = []
                sizes = []
                colors = []
                fontnames = []
                for line in block["lines"]:
                    line_text = "".join(span["text"] for span in line["spans"])
                    if line_text.strip():
                        text_parts.append(line_text)
                    for span in line["spans"]:
                        sizes.append(span["size"])
                        colors.append(span["color"])
                        fontnames.append(span["font"])

                flat_text = WHITESPACE_RE.sub(" ", " ".join(text_parts)).strip()
                if not flat_text:
                    continue

                raw_blocks.append(
                    {
                        "bbox": tuple(block["bbox"]),
                        "font_size": sum(sizes) / len(sizes) if sizes else 11.0,
                        "color_rgb": _srgb_int_to_rgb(colors[0] if colors else 0),
                        "fontname": _pick_helvetica_variant(fontnames[0] if fontnames else ""),
                        "text": flat_text,
                    }
                )

            for blk in _merge_touching_blocks(raw_blocks):
                self._blocks.append(
                    {
                        "page_index": page_index,
                        "bbox": blk["bbox"],
                        "font_size": blk["font_size"],
                        "color_rgb": blk["color_rgb"],
                        "fontname": blk["fontname"],
                    }
                )
                self.units.append(blk["text"])

    @classmethod
    def load(cls, path: str) -> "PdfDocument":
        return cls(path)

    def _measure_spare(self, width: float, height: float, text: str, fontsize: float, fontname: str) -> float:
        """Try a fontsize on a disposable scratch page and report the spare
        space, without touching the real page (see note in __init__ on why
        insert_textbox can't be trial-called against the real page)."""
        scratch_page = self._scratch_doc.new_page(width=width + 20, height=height + 20)
        rect = fitz.Rect(10, 10, 10 + width, 10 + height)
        spare = scratch_page.insert_textbox(rect, text, fontsize=fontsize, fontname=fontname)
        self._scratch_doc.delete_page(scratch_page.number)
        return spare

    def _insert_with_shrink(self, page, rect, text, base_fontsize, fontname, color, max_y):
        # insert_textbox's own "spare" return value is not trustworthy right
        # at the boundary: it can report a fit (spare >= 0) while the glyphs
        # it actually draws still extend past the given rectangle's bottom
        # edge (confirmed empirically - not a rounding hair, tens of points
        # in some cases). So "fits" here means spare comfortably clears a
        # margin, not just spare >= 0 - and the box is capped at max_y (the
        # top of the next block below, so we never grow into whatever
        # follows).
        safe_bottom = max(rect.y1, max_y - 4)
        bounded_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, safe_bottom)
        margin = max(4.0, bounded_rect.height * 0.08)

        best_fontsize = 2.0
        best_spare = float("-inf")
        fontsize = base_fontsize
        while fontsize >= 2.0:
            spare = self._measure_spare(bounded_rect.width, bounded_rect.height, text, fontsize, fontname)
            if spare > best_spare:
                best_spare, best_fontsize = spare, fontsize
            if spare >= margin:
                break
            fontsize -= FONTSIZE_STEP

        # Exactly one real draw on the actual page, at whichever fontsize
        # cleared the margin (or, failing that, came closest to fitting) -
        # so something is always visible rather than silently dropped, and
        # nothing ever gets drawn twice.
        page.insert_textbox(
            bounded_rect, text, fontsize=best_fontsize, fontname=fontname, color=color, align=0
        )

    def _compute_max_y_bounds(self) -> list[float]:
        """For each block, the y-coordinate it must not draw past: the top of
        the next block below it in the same column, or the page bottom."""
        by_page: dict[int, list[int]] = defaultdict(list)
        for i, blk in enumerate(self._blocks):
            by_page[blk["page_index"]].append(i)

        max_y = [0.0] * len(self._blocks)
        for page_index, indices in by_page.items():
            page_bottom = self.doc[page_index].rect.height - 18
            for i in indices:
                x0_i, y0_i, x1_i, y1_i = self._blocks[i]["bbox"]
                best = page_bottom
                for j in indices:
                    if j == i:
                        continue
                    x0_j, y0_j, x1_j, y1_j = self._blocks[j]["bbox"]
                    horizontal_overlap = min(x1_i, x1_j) - max(x0_i, x0_j)
                    if horizontal_overlap <= 0:
                        continue  # different column, ignore
                    if y0_j >= y1_i - 1:  # block j is below block i
                        best = min(best, y0_j)
                for x0_o, y0_o, x1_o, y1_o in self._obstacles.get(page_index, []):
                    horizontal_overlap = min(x1_i, x1_o) - max(x0_i, x0_o)
                    if horizontal_overlap <= 0:
                        continue
                    if y0_o >= y1_i - 1:
                        best = min(best, y0_o)
                max_y[i] = min(best, page_bottom)
        return max_y

    def rebuild(self, translations: list[str], output_path: str) -> None:
        pages_touched = set()
        for blk in self._blocks:
            page = self.doc[blk["page_index"]]
            rect = fitz.Rect(blk["bbox"])
            page.add_redact_annot(rect, fill=(1, 1, 1))
            pages_touched.add(blk["page_index"])

        for page_index in pages_touched:
            self.doc[page_index].apply_redactions()

        max_y_bounds = self._compute_max_y_bounds()

        for blk, translated, max_y in zip(self._blocks, translations, max_y_bounds):
            page = self.doc[blk["page_index"]]
            rect = fitz.Rect(blk["bbox"])
            self._insert_with_shrink(
                page,
                rect,
                _sanitize_for_pdf_font(translated),
                blk["font_size"],
                blk["fontname"],
                blk["color_rgb"],
                max_y,
            )

        self.doc.save(output_path, garbage=4, deflate=True)
        self._scratch_doc.close()
