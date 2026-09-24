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


def test_orders_items_by_exact_worksheet_sequence_without_a_pdf():
    pdf_codes = [
        "42.10.20100", "53610013", "832804", "H7493893101J0",
        "H74938969720", "H7493919320200", "H7493919320250",
        "H7493966628300", "H7493966632300", "LP-P-30S-YNP20",
        "LPJD503N", "M00146154B0",
    ]
    base_order = [
        "H7493893101J0", "53610013", "42.10.20100", "832804",
        "H74938969720", "M00146154B0", "H7493966628300",
        "H7493966632300", "H7493919320200", "H7493919320250",
        "LP-P-30S-YNP20", "LPJD503N",
    ]
    pdf_items = [item(position, code, Decimal(position)) for position, code in enumerate(pdf_codes, 1)]

    sorted_items, missing = order_items(pdf_items, base_order)

    assert missing == 0
    assert [product.code for product in sorted_items] == base_order
    assert [product.original_order for product in sorted_items] == [4, 2, 1, 3, 5, 12, 8, 9, 6, 7, 10, 11]
