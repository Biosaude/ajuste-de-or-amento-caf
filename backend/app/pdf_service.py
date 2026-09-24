import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import fitz

from .models import PdfItem
from .normalize import clean_text, money, normalize_code

LOGGER = logging.getLogger(__name__)
Y_TOLERANCE = 2.0


class PdfError(ValueError):
    pass


@dataclass(slots=True)
class ParsedPdf:
    budget_number: str
    patient: str
    total: str
    items: list[PdfItem]
    expected_item_count: int | None = None
    subtotal: str = "Não identificado"
    total_units: str = "Não identificado"
    total_products: str = "Não identificado"


@dataclass(frozen=True, slots=True)
class PositionedText:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) / 2


def _field(text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:º°]?\s*([^\n|]+)", text, re.IGNORECASE)
    return clean_text(match.group(1)) if match else "Não identificado"


def _summary_number(text: str, label: str) -> int | None:
    match = re.search(rf"{label}\s*[.:]*\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _label(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", value)


def _words(page: fitz.Page) -> list[PositionedText]:
    return [PositionedText(str(w[4]), float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            for w in page.get_text("words", sort=True) if clean_text(w[4])]


def _dict_words(page: fitz.Page) -> list[PositionedText]:
    """Fallback based on spans/lines, independent from get_text('words')."""
    result: list[PositionedText] = []
    for block in page.get_text("dict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = clean_text(span.get("text", ""))
                if not text:
                    continue
                x0, y0, x1, y1 = map(float, span["bbox"])
                # Split spans so a renderer that emitted a whole row in one span
                # can still be assigned to dynamic header columns.
                pieces = list(re.finditer(r"\S+", text))
                width = max(x1 - x0, 0.1)
                for piece in pieces:
                    px0 = x0 + width * piece.start() / len(text)
                    px1 = x0 + width * piece.end() / len(text)
                    result.append(PositionedText(piece.group(), px0, y0, px1, y1))
    return result


def _groups_y(words: Iterable[PositionedText], tolerance: float = Y_TOLERANCE) -> list[list[PositionedText]]:
    groups: list[list[PositionedText]] = []
    for word in sorted(words, key=lambda w: (w.y, w.x0)):
        if not groups or abs(word.y - sum(x.y for x in groups[-1]) / len(groups[-1])) > tolerance:
            groups.append([word])
        else:
            groups[-1].append(word)
    for group in groups:
        group.sort(key=lambda word: word.x0)
    return groups


HEADER_MATCHERS = {
    "item": lambda s: s == "item",
    "code": lambda s: s in {"codigo", "cod"},
    "description": lambda s: s.startswith("descricao"),
    "anvisa": lambda s: "anvisa" in s,
    "validity": lambda s: s.startswith("validade"),
    "brand": lambda s: s == "marca",
    "quantity": lambda s: s in {"qtde", "qtd", "quantidade"},
    "unit_value": lambda s: s in {"vrunit", "vrunitario", "valorunit", "valorunitario"},
    "total_value": lambda s: s in {"vrtotal", "valortotal"},
}


def _find_header(words: list[PositionedText]) -> tuple[dict[str, float], float, list[str]] | None:
    groups = _groups_y(words, 5.0)
    for index, group in enumerate(groups):
        # Some VIMAN exports wrap a header cell. Include adjacent header baselines.
        following = groups[index + 1] if index + 1 < len(groups) else []
        following_is_header = any(
            matcher(_label(word.text))
            for word in following
            for matcher in HEADER_MATCHERS.values()
        )
        band = group + (following if following_is_header and
                        following[0].y - group[0].y <= 12 else [])
        found: dict[str, float] = {}
        normalized = [_label(word.text) for word in band]
        for pos, word in enumerate(band):
            for name, matches in HEADER_MATCHERS.items():
                if name not in found and matches(normalized[pos]):
                    found[name] = word.x
            # Only value headings need adjacent-token composition ("Vr." +
            # "unit."). Combining every pair made "produto Reg.ANVISA" claim
            # the description's X position as the ANVISA anchor.
            if pos + 1 < len(band):
                joined = normalized[pos] + normalized[pos + 1]
                for name in ("unit_value", "total_value"):
                    if name not in found and HEADER_MATCHERS[name](joined):
                        found[name] = word.x
        required = {"item", "code", "description", "anvisa", "quantity"}
        if required <= found.keys() and found["item"] < found["code"] < found["description"]:
            ordered = sorted(found.items(), key=lambda pair: pair[1])
            # Require monotonically recognizable table columns, but tolerate
            # optional/missing ANVISA, validity, and brand labels.
            return dict(ordered), max(word.y1 for word in band) + 0.5, [word.text for word in band]
    return None


def _column_bounds(anchors: dict[str, float], page_width: float) -> dict[str, tuple[float, float]]:
    ordered = sorted(anchors.items(), key=lambda pair: pair[1])
    bounds: dict[str, tuple[float, float]] = {}
    for index, (name, center) in enumerate(ordered):
        left = 0.0 if index == 0 else (ordered[index - 1][1] + center) / 2
        right = page_width if index == len(ordered) - 1 else (center + ordered[index + 1][1]) / 2
        bounds[name] = (left, right)
    return bounds


def _column_text(words: Iterable[PositionedText], bounds: tuple[float, float]) -> str:
    left, right = bounds
    selected = sorted((word for word in words if left <= word.x < right), key=lambda word: (word.y, word.x0))
    return clean_text(" ".join(word.text for word in selected))


def _subtotal_y(words: list[PositionedText], after: float, page_height: float) -> float:
    candidates = [word.y0 for word in words if word.y > after and _label(word.text).startswith("subtotal")]
    return min(candidates, default=page_height - 20.0)


def _extract_page(page: fitz.Page, page_no: int, words: list[PositionedText]) -> tuple[list[PdfItem], dict]:
    header = _find_header(words)
    if not header:
        return [], {"page": page_no, "reason": "table header not found", "headers": []}
    anchors, table_top, header_words = header
    table_bottom = _subtotal_y(words, table_top, page.rect.height)
    region = [word for word in words if table_top < word.y < table_bottom]
    groups = _groups_y(region)
    bounds = _column_bounds(anchors, page.rect.width)
    starts: list[tuple[float, int, str]] = []
    rejected: list[str] = []
    for group in groups:
        item_text = _column_text(group, bounds["item"])
        code_text = _column_text(group, bounds["code"])
        item_match = re.fullmatch(r"\s*(\d{1,4})\s*", item_text)
        if item_match and code_text and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-_/]*", code_text):
            starts.append((sum(word.y for word in group) / len(group), int(item_match.group(1)), code_text))
        elif item_text or code_text:
            rejected.append(f"y={group[0].y:.1f}: item={item_text!r}, code={code_text!r}")

    items: list[PdfItem] = []
    for index, (center_y, item_number, _) in enumerate(starts):
        top = table_top if index == 0 else (starts[index - 1][0] + center_y) / 2
        bottom = table_bottom if index == len(starts) - 1 else (center_y + starts[index + 1][0]) / 2
        row_words = [word for word in region if top <= word.y < bottom]
        code = _column_text(row_words, bounds["code"])
        description = _column_text(row_words, bounds["description"])
        anvisa = _column_text(row_words, bounds["anvisa"]) if "anvisa" in bounds else ""
        validity = _column_text(row_words, bounds["validity"]) if "validity" in bounds else ""
        brand = _column_text(row_words, bounds["brand"]) if "brand" in bounds else ""
        quantity_text = _column_text(row_words, bounds["quantity"])
        unit_text = _column_text(row_words, bounds["unit_value"]) if "unit_value" in bounds else ""
        total_text = _column_text(row_words, bounds["total_value"]) if "total_value" in bounds else ""
        item_left, item_right = bounds["item"]
        rect = fitz.Rect(0, max(table_top, min((word.y0 for word in row_words), default=top) - 1),
                         page.rect.width, min(table_bottom, max((word.y1 for word in row_words), default=bottom) + 1))
        item_rect = fitz.Rect(item_left, rect.y0, item_right, rect.y1)
        tokens = tuple(word.text for word in sorted(row_words, key=lambda word: (word.y, word.x0)))
        items.append(PdfItem(item_number, code, normalize_code(code), description, page_no, rect, item_rect,
                             money(quantity_text), money(unit_text), money(total_text), tokens,
                             anvisa=anvisa, validity=validity, brand=brand))
    debug = {"page": page_no, "table_top": round(table_top, 2), "table_bottom": round(table_bottom, 2),
             "headers": header_words, "y_groups": len(groups), "candidate_rows": len(starts),
             "codes": [item.code for item in items], "rejected": rejected}
    return items, debug


def _extract_items(doc: fitz.Document, method: str) -> tuple[list[PdfItem], list[dict]]:
    items: list[PdfItem] = []
    debug: list[dict] = []
    extractor = _words if method == "words" else _dict_words
    for page_no, page in enumerate(doc):
        page_items, page_debug = _extract_page(page, page_no, extractor(page))
        items.extend(page_items)
        debug.append(page_debug)
    # original_order is the document-wide order, regardless of page numbering.
    for order, item in enumerate(items, 1):
        item.original_order = order
    return items, debug


def _log_debug(method: str, expected: int | None, items: list[PdfItem], debug: list[dict]) -> None:
    if os.getenv("VERCEL"):
        return
    LOGGER.debug("VIMAN parser method=%s expected=%s parsed=%d codes=%s pages=%s",
                 method, expected, len(items), [item.code for item in items], debug)


def parse_pdf(path: Path) -> ParsedPdf:
    doc = fitz.open(path)
    try:
        all_text = "\n".join(page.get_text("text") for page in doc)
        if not all_text.strip():
            raise PdfError("O PDF não possui camada de texto pesquisável; OCR não é executado automaticamente.")
        expected = _summary_number(all_text, r"itens\s+or[çc]ados")
        attempts: list[tuple[str, list[PdfItem], list[dict]]] = []
        for method in ("words", "dict"):
            items, debug = _extract_items(doc, method)
            attempts.append((method, items, debug))
            _log_debug(method, expected, items, debug)
            if items and (expected is None or len(items) == expected):
                break
        else:
            method, items, debug = max(attempts, key=lambda attempt: len(attempt[1]))

        if not items:
            LOGGER.warning("VIMAN table not parsed: expected=%s attempts=%s", expected,
                           [{"method": name, "pages": details} for name, _, details in attempts])
            raise PdfError("Não foi possível identificar as linhas da tabela de itens neste modelo de PDF.")
        if expected is not None and len(items) != expected:
            codes = ", ".join(item.code for item in items) or "nenhum"
            LOGGER.warning("VIMAN item count mismatch: expected=%d parsed=%d codes=%s debug=%s",
                           expected, len(items), codes, debug)
            raise PdfError(f"Não foi possível identificar todos os itens. Esperados: {expected}. "
                           f"Identificados: {len(items)}. Códigos identificados: {codes}.")
        return ParsedPdf(
            _field(all_text, r"or[çc]amento(?:\s*n[úu]mero)?|proposta"),
            _field(all_text, r"paciente"),
            _field(all_text, r"total\s*geral"),
            items,
            expected,
            _field(all_text, r"subtotal"),
            _field(all_text, r"total\s+de\s+unidades|total\s+unidades"),
            _field(all_text, r"total\s+produtos"),
        )
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
    fingerprint = lambda i: (i.normalized_code, i.anvisa, i.validity, i.brand,
                             i.quantity, i.unit_value, i.total_value)
    if len(original) != len(ordered):
        errors.append("Quantidade de itens foi alterada.")
    if Counter(fingerprint(i) for i in original) != Counter(fingerprint(i) for i in ordered):
        errors.append("Códigos, quantidades ou valores divergem.")
    if sum((i.quantity or 0) for i in original) != sum((i.quantity or 0) for i in ordered):
        errors.append("Quantidade total de unidades diverge.")
    if sum((i.total_value or 0) for i in original) != sum((i.total_value or 0) for i in ordered):
        errors.append("Soma dos valores totais diverge.")
    return errors


def document_integrity_errors(original: ParsedPdf, generated: ParsedPdf) -> list[str]:
    errors = integrity_errors(original.items, generated.items)
    if original.subtotal != generated.subtotal:
        errors.append("Subtotal do documento foi alterado.")
    if original.total != generated.total:
        errors.append("Total geral do documento foi alterado.")
    if original.total_units != generated.total_units:
        errors.append("Total de unidades do documento foi alterado.")
    if original.total_products != generated.total_products:
        errors.append("Total de produtos do documento foi alterado.")
    return errors


def _insert_item_number(page: fitz.Page, slot: PdfItem, number: int) -> None:
    # insert_textbox silently omits text when a compact VIMAN row is a fraction
    # shorter than its font line-height. A baseline insertion is deterministic.
    font_size = max(4.0, min(8.0, slot.item_rect.height * 0.62))
    text = str(number)
    width = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
    x = slot.item_rect.x0 + max(0.0, (slot.item_rect.width - width) / 2)
    baseline = slot.item_rect.y1 - max(0.8, (slot.item_rect.height - font_size) / 2)
    page.insert_text((x, baseline), text, fontsize=font_size, fontname="helv")


def create_reordered_pdf(source: Path, destination: Path, original: list[PdfItem], ordered: list[PdfItem]) -> None:
    src = fitz.open(source)
    out = fitz.open(source)
    try:
        slots = list(original)
        # Redact each detected row only: headers, subtotal and document content
        # outside item slots never enter a redaction rectangle.
        for slot in slots:
            out[slot.page].add_redact_annot(slot.rect, fill=(1, 1, 1))
        for page in out:
            page.apply_redactions()
        for new_number, (slot, item) in enumerate(zip(slots, ordered), 1):
            target = out[slot.page]
            source_clip = fitz.Rect(item.item_rect.x1, item.rect.y0, item.rect.x1, item.rect.y1)
            target_rect = fitz.Rect(slot.item_rect.x1, slot.rect.y0, slot.rect.x1, slot.rect.y1)
            target.show_pdf_page(target_rect, src, item.page, clip=source_clip,
                                 keep_proportion=False, overlay=True)
            _insert_item_number(target, slot, new_number)
        out.save(destination, garbage=4, deflate=True)
    finally:
        src.close()
        out.close()
