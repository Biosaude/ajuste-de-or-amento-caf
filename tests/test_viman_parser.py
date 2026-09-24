from pathlib import Path

import fitz
import pytest
from openpyxl import Workbook

from backend.app.excel_service import parse_spreadsheet
from backend.app.pdf_service import (
    create_reordered_pdf,
    document_integrity_errors,
    order_items,
    parse_pdf,
)

PDF_CODES = [
    "42.10.20100", "53610013", "832804", "H7493893101J0",
    "H74938969720", "H7493919320200", "H7493919320250",
    "H7493966628300", "H7493966632300", "LP-P-30S-YNP20",
    "LPJD503N", "M00146154B0",
]
EXCEL_ORDER = [
    "H7493893101J0", "53610013", "42.10.20100", "832804",
    "H74938969720", "M00146154B0", "H7493966628300",
    "H7493966632300", "H7493919320200", "H7493919320250",
    "LP-P-30S-YNP20", "LPJD503N",
]
ANVISA = {code: str(80102519060 + index) for index, code in enumerate(PDF_CODES, 1)}
ANVISA["42.10.20100"] = "81506640009"
ANVISA["53610013"] = "80446140038"
ANVISA["H7493893101J0"] = "10341350351"
ANVISA["H7493966628300"] = "10341351061"


def make_real_layout_viman_pdf(path: Path) -> None:
    """Regression fixture using the measured A4 coordinates from the real PDF."""
    document = fitz.open()
    page = document.new_page(width=595.28, height=841.89)
    page.insert_text((25.20, 80), "Orçamento número: 69.662", fontsize=8)
    page.insert_text((25.20, 95), "Paciente: BENEDITO DIAS COUTINHO", fontsize=8)
    columns = [25.20, 46.20, 113.41, 252.03, 319.24, 357.05, 441.06, 466.27, 529.27]
    headers = ["Item", "Código", "Descrição do produto", "Reg.ANVISA", "Validade", "Marca", "Qtde", "Vr.unit.", "Vr.total"]
    # At fontsize 5, insertion baseline 317.94 yields a word y0 close to 312.56.
    for x, header in zip(columns, headers):
        page.insert_text((x, 317.94), header, fontsize=5)

    totals = ["200,00", "300,00", "400,00", "1.000,00", "1.100,00", "1.200,00",
              "1.300,00", "1.400,00", "1.500,00", "1.600,00", "1.700,00", "1.590,00"]
    descriptions = ["INTRODUTOR RADIAL 6FR X 11 CM"] + [f"PRODUTO VIMAN {index}" for index in range(2, 13)]
    descriptions[3] = "FIO GUIA 14X185 J-TIP PT2 .LS"
    for index, (code, total, description) in enumerate(zip(PDF_CODES, totals, descriptions), 1):
        # Measured real y0 values are 337.23 + (index - 1) * 11.25.
        baseline = 342.61 + (index - 1) * 11.25
        quantity = "2" if index in {4, 6} else "1"
        brand = "BOSTON" if index == 4 else ("APT MEDICAL" if index == 8 else "EPTCA")
        validity = "23/12/34" if index == 4 else "Vigente"
        unit_value = "500,00" if index == 4 else total
        values = [str(index), code, description, ANVISA[code], validity, brand, quantity, unit_value, total]
        for x, value in zip(columns, values):
            page.insert_text((x, baseline), value, fontsize=5)

    # Baseline offset produces y0 close to the measured 473.30.
    page.insert_text((25.20, 482.98), "Subtotal: 13.290,00", fontsize=9)
    page.insert_text((25.20, 497.30), "Itens orçados....: 12", fontsize=9)
    page.insert_text((25.20, 512.30), "Total de unidades: 14", fontsize=9)
    page.insert_text((25.20, 527.30), "Total produtos: 13.290,00", fontsize=9)
    page.insert_text((25.20, 542.30), "TOTAL GERAL: 13.290,00", fontsize=9)
    document.save(path)
    document.close()


def make_real_layout_spreadsheet(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "COTAÇÃO"
    for _ in range(7):
        sheet.append([])
    sheet.cell(8, 6, "ANVISA - REFERÊNCIA")
    sheet.cell(8, 7, "Descrição")
    for row, code in enumerate(EXCEL_ORDER, 9):
        spaces = "  " if code == "H7493966628300" else " "
        sheet.cell(row, 6, f"{ANVISA[code]} -{spaces}{code}")
        sheet.cell(row, 7, f"Produto {code}")
    workbook.save(path)
    workbook.close()


def test_real_viman_coordinates_excel_crossing_and_pdf_integrity(tmp_path):
    source = tmp_path / "orcamento-viman.pdf"
    output = tmp_path / "orcamento-ordenado.pdf"
    spreadsheet = tmp_path / "BENEDITO DIAS COUTINHO.xlsx"
    make_real_layout_viman_pdf(source)
    make_real_layout_spreadsheet(spreadsheet)

    with fitz.open(source) as fixture:
        words = fixture[0].get_text("words")
        word_y = lambda text: next(word[1] for word in words if word[4] == text and word[0] < 45)
        assert fixture[0].rect.width == pytest.approx(595.28, abs=0.01)
        assert fixture[0].rect.height == pytest.approx(841.89, abs=0.01)
        assert word_y("Item") == pytest.approx(312.56, abs=0.02)
        assert word_y("1") == pytest.approx(337.23, abs=0.02)
        assert word_y("12") == pytest.approx(460.98, abs=0.02)
        assert word_y("Subtotal:") == pytest.approx(473.30, abs=0.02)

    parsed = parse_pdf(source)
    assert parsed.expected_item_count == 12
    assert len(parsed.items) == 12
    assert [item.code for item in parsed.items] == PDF_CODES
    assert parsed.items[0].description == "INTRODUTOR RADIAL 6FR X 11 CM"
    assert parsed.items[0].anvisa == "81506640009"
    assert parsed.items[3].brand == "BOSTON"
    assert parsed.items[3].quantity == 2

    products = parse_spreadsheet(spreadsheet)
    base_order = [product.code for product in products]
    assert base_order == EXCEL_ORDER
    ordered, unmatched = order_items(parsed.items, [product.normalized_code for product in products])
    assert len(products) == 12
    assert len(ordered) - unmatched == 12
    assert unmatched == 0
    assert [item.original_order for item in ordered] == [4, 2, 1, 3, 5, 12, 8, 9, 6, 7, 10, 11]
    assert [item.code for item in ordered] == EXCEL_ORDER
    assert document_integrity_errors(parsed, parsed, ordered) == [
        "A ordem dos itens no PDF final diverge da planilha base."
    ]

    create_reordered_pdf(source, output, parsed.items, ordered)
    generated = parse_pdf(output)
    assert [item.code for item in generated.items] == EXCEL_ORDER
    assert document_integrity_errors(parsed, generated, ordered) == []
    first = generated.items[0]
    assert first.original_order == 1
    assert first.code == "H7493893101J0"
    assert first.description == "FIO GUIA 14X185 J-TIP PT2 .LS"
    assert first.anvisa == "10341350351"
    assert first.validity == "23/12/34"
    assert first.brand == "BOSTON"
    assert first.quantity == 2
    assert first.unit_value == 500
    assert first.total_value == 1000
    assert generated.expected_item_count == parsed.expected_item_count == 12
    assert generated.subtotal == parsed.subtotal == "13.290,00"
    assert generated.total_units == parsed.total_units == "14"
    assert generated.total_products == parsed.total_products == "13.290,00"
    assert generated.total == parsed.total == "13.290,00"
    assert output.read_bytes().startswith(b"%PDF")
