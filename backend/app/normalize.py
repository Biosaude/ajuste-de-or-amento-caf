import re
import unicodedata
from decimal import Decimal, InvalidOperation


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\u200b", "")
    return " ".join(text.split()).strip()


def normalize_code(value: object) -> str:
    """Normalize spacing/case without changing meaningful code characters.

    Dots, hyphens, letters and leading zeroes are part of a material reference;
    removing any of them can create both false negatives and false positives.
    """
    if value is None:
        return ""
    # NFKC makes visually equivalent forms (for example full-width Latin
    # characters copied from a PDF) comparable without touching punctuation.
    text = unicodedata.normalize("NFKC", str(value)).upper()
    # PDF text layers may contain formatting controls which are neither visible
    # nor matched by ``\s``.  Remove those together with all whitespace, while
    # deliberately preserving every printable character in the reference.
    return "".join(
        character
        for character in text
        if not character.isspace() and unicodedata.category(character) != "Cf"
    )


def money(value: str) -> Decimal | None:
    candidate = re.sub(r"[^0-9,.-]", "", value)
    if not candidate:
        return None
    if "," in candidate:
        candidate = candidate.replace(".", "").replace(",", ".")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None
