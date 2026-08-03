import re

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


class TxtDocument:
    """Plain text document split into paragraphs for translation."""

    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.units: list[str] = [p for p in PARAGRAPH_SPLIT.split(raw_text) if p.strip()]

    @classmethod
    def load(cls, path: str) -> "TxtDocument":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return cls(f.read())

    def rebuild(self, translations: list[str], output_path: str) -> None:
        content = "\n\n".join(translations)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
