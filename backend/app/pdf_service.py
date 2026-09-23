import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import fitz

from .models import PdfItem
from .normalize import clean_text, money, normalize_code


class PdfError(ValueError):
    pass


@dataclass(slots=True)
class ParsedPdf:
    budget_number: str
    patient: str
    total: str
    items: list[PdfItem]


def _field(text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:º°]?\s*([^\n|]+)", text, re.IGNORECASE)
    return clean_text(match.group(1)) if match else "Não identificado"


def parse_pdf(path: Path) -> ParsedPdf:
    doc = fitz.open(path)
    try:
        all_text = "\n".join(page.get_text("text") for page in doc)
        if not all_text.strip():
            raise PdfError("O PDF não possui camada de texto pesquisável; OCR não é executado automaticamente.")
        budget = _field(all_text, r"or[çc]amento(?:\s*n[úu]mero)?|proposta")
        patient = _field(all_text, r"paciente")
        total = _field(all_text, r"total\s*geral")
        items: list[PdfItem] = []
        for page_no, page in enumerate(doc):
            words = page.get_text("words", sort=True)
            header_words = [w for w in words if w[4].lower().rstrip(".:") in {"item", "código", "codigo", "descrição", "descricao"}]
            if not header_words:
                continue
            header_y = min(w[1] for w in header_words)
            footer_candidates = [w[1] for w in words if w[1] > header_y and re.match(r"^(subtotal|total)$", w[4], re.I)]
            bottom = min(footer_candidates, default=page.rect.height - 45)
            table_words = [w for w in words if header_y + 4 < w[1] < bottom]
            lines: dict[float, list[tuple]] = {}
            for word in table_words:
                key = round(word[1] / 3) * 3
                lines.setdefault(key, []).append(word)
            candidates = []
            for y, line in sorted(lines.items()):
                line.sort(key=lambda w: w[0])
                if line and re.fullmatch(r"\d{1,3}", line[0][4]):
                    candidates.append((y, line))
            for idx, (y, line) in enumerate(candidates):
                next_y = candidates[idx + 1][0] if idx + 1 < len(candidates) else bottom
                row_words = [w for w in table_words if y - 2 <= w[1] < next_y - 1]
                row_words.sort(key=lambda w: (w[1], w[0]))
                if len(line) < 2:
                    continue
                code = clean_text(line[1][4])
                rect = fitz.Rect(0, max(header_y + 3, y - 2), page.rect.width, next_y - 1)
                item_rect = fitz.Rect(line[0][0] - 1, rect.y0, line[0][2] + 2, rect.y1)
                tokens = tuple(w[4] for w in row_words)
                numeric = [money(t) for t in tokens if money(t) is not None]
                description = " ".join(w[4] for w in line[2:] if money(w[4]) is None)
                items.append(PdfItem(len(items) + 1, code, normalize_code(code), description, page_no, rect, item_rect,
                                     numeric[-3] if len(numeric) >= 3 else None,
                                     numeric[-2] if len(numeric) >= 2 else None,
                                     numeric[-1] if numeric else None, tokens))
        if not items:
            raise PdfError("Não foi possível identificar as linhas da tabela de itens neste modelo de PDF.")
        return ParsedPdf(budget, patient, total, items)
    finally:
        doc.close()


def order_items(items: list[PdfItem], order: list[str]) -> tuple[list[PdfItem], int]:
    ranks = {code: index for index, code in enumerate(order)}
    found = [item for item in items if item.normalized_code in ranks]
    missing = [item for item in items if item.normalized_code not in ranks]
    found.sort(key=lambda item: (ranks[item.normalized_code], item.original_order))
    return found + missing, len(missing)


def integrity_errors(original: list[PdfItem], ordered: list[PdfItem]) -> list[str]:
    errors: list[str] = []
    fingerprint = lambda i: (i.normalized_code, i.quantity, i.unit_value, i.total_value, i.words[1:])
    if len(original) != len(ordered):
        errors.append("Quantidade de itens foi alterada.")
    if Counter(fingerprint(i) for i in original) != Counter(fingerprint(i) for i in ordered):
        errors.append("Códigos, quantidades, descrições ou valores divergem.")
    if sum((i.quantity or 0) for i in original) != sum((i.quantity or 0) for i in ordered):
        errors.append("Quantidade total de unidades diverge.")
    if sum((i.total_value or 0) for i in original) != sum((i.total_value or 0) for i in ordered):
        errors.append("Soma dos valores totais diverge.")
    return errors


def create_reordered_pdf(source: Path, destination: Path, original: list[PdfItem], ordered: list[PdfItem]) -> None:
    src = fitz.open(source)
    out = fitz.open(source)
    try:
        slots = [item for item in original]
        # Cover each original slot, preserving everything outside item rows.
        for slot in slots:
            page = out[slot.page]
            page.add_redact_annot(slot.rect, fill=(1, 1, 1))
        for page in out:
            page.apply_redactions()
        for new_number, (slot, item) in enumerate(zip(slots, ordered), 1):
            target = out[slot.page]
            # Copy the complete vector row from the original (including borders),
            # then cover only the number glyph inside the first cell.
            target.show_pdf_page(slot.rect, src, item.page, clip=item.rect, keep_proportion=False, overlay=True)
            number_cover = fitz.Rect(slot.item_rect.x0 + 1, slot.item_rect.y0 + 1,
                                     slot.item_rect.x1 - 1, slot.item_rect.y1 - 1)
            target.draw_rect(number_cover, color=None, fill=(1, 1, 1), overlay=True)
            target.insert_textbox(slot.item_rect, str(new_number), fontsize=8, fontname="helv", align=fitz.TEXT_ALIGN_CENTER)
        out.save(destination, garbage=4, deflate=True)
    finally:
        src.close()
        out.close()
