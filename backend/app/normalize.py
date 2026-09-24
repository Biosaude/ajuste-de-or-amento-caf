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
    text = str(value).replace("\u00a0", " ").strip().upper()
    return re.sub(r"\s+", "", text)


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
