import re
import unicodedata
from decimal import Decimal, InvalidOperation


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\u200b", "")
    return " ".join(text.split()).strip()


def normalize_code(value: object) -> str:
    text = clean_text(value).upper()
    # Excel commonly turns an identifier into 123.0. Preserve other punctuation.
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    compact = re.sub(r"[^A-Z0-9]", "", text)
    if compact.isdigit():
        compact = compact.lstrip("0") or "0"
    return compact


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

