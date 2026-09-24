from dataclasses import dataclass, field
from decimal import Decimal

import fitz


@dataclass(slots=True)
class BaseProduct:
    code: str
    normalized_code: str
    description: str = ""


@dataclass(slots=True)
class PdfItem:
    original_order: int
    code: str
    normalized_code: str
    description: str
    page: int
    rect: fitz.Rect
    item_rect: fitz.Rect
    quantity: Decimal | None = None
    unit_value: Decimal | None = None
    total_value: Decimal | None = None
    words: tuple[str, ...] = field(default_factory=tuple)
    anvisa: str = ""
    validity: str = ""
    brand: str = ""
