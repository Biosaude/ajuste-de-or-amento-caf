from pathlib import Path

import fitz
from openpyxl import Workbook

from backend.app.excel_service import parse_spreadsheet
from backend.app.pdf_service import (
    create_reordered_pdf,
    document_integrity_errors,
    order_items,
    parse_pdf,
)

CODES = [
    "42.10.20100",
    "53610013",
    "832804",
    "H7493893101J0",
    "H74938969720",
    "H7493919320200",
    "H7493919320250",
    "H7493966628300",
    "H7493966632300",
    "LP-P-30S-YNP20",
    "LPJD503N",
    "M00146154B0",
]
# Deliberately different from PDF order: this is the sequence supplied by Excel.
EXCEL_ORDER = [CODES[2], CODES[0], CODES[10], CODES[1], *CODES[3:10], CODES[11]]


def make_viman_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=842, height=595)
    page.insert_text((35, 35), "Orçamento número: 69.662", fontsize=10)
    page.insert_text((35, 50), "Paciente: BENEDITO DIAS COUTINHO", fontsize=10)
    columns = [35, 70, 155, 390, 485, 550, 625, 680, 755]
    headers = ["Item", "Código", "Descrição do produto", "Reg. ANVISA", "Validade", "Marca", "Qtde", "Vr. unit.", "Vr. total"]
    for x, header in zip(columns, headers):
        page.insert_text((x, 85), header, fontsize=7)
    totals = ["200,00", "300,00", "400,00", "1.000,00", "1.100,00", "1.200,00",
              "1.300,00", "1.400,00", "1.500,00", "1.600,00", "1.700,00", "1.590,00"]
    for index, (code, total) in enumerate(zip(CODES, totals), 1):
        y = 100 + index * 15
        values = [str(index), code, f"PRODUTO VIMAN {index}", str(81506640000 + index),
                  "Vigente", "MARCA", "1", total, total]
        for x, value in zip(columns, values):
            page.insert_text((x, y), value, fontsize=7)
    page.insert_text((35, 310), "Subtotal: 13.290,00", fontsize=9)
    page.insert_text((35, 325), "Itens orçados....: 12", fontsize=9)
    page.insert_text((35, 340), "Total de unidades: 12", fontsize=9)
    page.insert_text((35, 355), "TOTAL GERAL: 13.290,00", fontsize=9)
    document.save(path)
    document.close()


def make_spreadsheet(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Código", "Descrição"])
    for code in EXCEL_ORDER:
        sheet.append([code, f"Produto {code}"])
    workbook.save(path)
    workbook.close()


def test_viman_parser_finds_12_items_and_preserves_document_totals(tmp_path):
    source = tmp_path / "vimam.pdf"
    output = tmp_path / "ordenado.pdf"
    spreadsheet = tmp_path / "base.xlsx"
    make_viman_pdf(source)
    make_spreadsheet(spreadsheet)

    parsed = parse_pdf(source)
    assert parsed.expected_item_count == 12
    assert len(parsed.items) == 12
    assert [item.code for item in parsed.items] == CODES
    assert parsed.items[0].description == "PRODUTO VIMAN 1"
    assert parsed.items[0].anvisa == "81506640001"
    assert parsed.items[0].validity == "Vigente"
    assert parsed.items[0].brand == "MARCA"

    products = parse_spreadsheet(spreadsheet)
    ordered, missing = order_items(parsed.items, [product.normalized_code for product in products])
    assert missing == 0
    assert [item.code for item in ordered] == EXCEL_ORDER

    create_reordered_pdf(source, output, parsed.items, ordered)
    generated = parse_pdf(output)
    assert [item.code for item in generated.items] == EXCEL_ORDER
    assert document_integrity_errors(parsed, generated) == []
    assert generated.subtotal == parsed.subtotal == "13.290,00"
    assert generated.total == parsed.total == "13.290,00"
    assert generated.total_units == parsed.total_units == "12"
