from decimal import Decimal

from backend.app.models import PdfItem
from backend.app.normalize import normalize_code
from backend.app.pdf_service import integrity_errors, order_items
import fitz


def item(order, code, total):
    return PdfItem(order, code, normalize_code(code), code, 0, fitz.Rect(0, order, 10, order+1), fitz.Rect(0, order, 1, order+1), Decimal(1), total, total, (str(order), code, str(total)))


def test_normalizes_excel_number_and_formatting():
    assert normalize_code("  00042.0\u200b") == "42"
    assert normalize_code("ab-12.30") == "AB1230"


def test_orders_missing_last_stably_and_keeps_integrity():
    original = [item(1, "A", Decimal("2")), item(2, "X", Decimal("3")), item(3, "B", Decimal("4"))]
    ordered, missing = order_items(original, ["B", "A"])
    assert [x.code for x in ordered] == ["B", "A", "X"]
    assert missing == 1
    assert integrity_errors(original, ordered) == []
