"""PDF invoice text extraction and conservative field parsing."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

FIELD_LABELS = {
    "invoice_number": "invoice number",
    "customer_name": "customer name",
    "invoice_date": "invoice date",
    "due_date": "due date",
    "amount": "amount",
}


def read_pdf_text(path: Path) -> str:
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception as error:
                raise ValueError("This PDF is password-protected. Remove its password and upload it again.") from error
            if not unlocked:
                raise ValueError("This PDF is password-protected. Remove its password and upload it again.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as error:
        raise ValueError("This PDF could not be opened. It may be damaged or use an unsupported format.") from error


def _find_value(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip(" \t:#-")
    return ""


def _normalise_date(value: str) -> str:
    value = value.strip().replace(",", "")
    formats = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y", "%b %d %Y", "%B %d %Y")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return ""


def extract_invoice_fields(text: str) -> tuple[dict[str, str], list[str]]:
    field_marker = (
        r"(?:invoice\s*(?:number|no\.?|#|date)|bill\s*to|billed\s*to|customer|client|"
        r"due\s*date|payment\s*due|amount\s*due|total\s*due|grand\s*total|invoice\s*total|total)"
    )
    # PDF text extractors sometimes concatenate adjacent text blocks without
    # inserting line breaks. Restore boundaries before known invoice labels.
    text = re.sub(rf"(?i)(?=\b{field_marker}\s*[:#-])", "\n", text)
    invoice_number = _find_value(
        text,
        (
            r"invoice\s*(?:number|no\.?|#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{1,79})",
            r"\binvoice\s*[:#-]\s*([A-Z0-9][A-Z0-9/_-]{1,79})",
        ),
    )
    customer_name = _find_value(
        text,
        (r"(?:bill\s*to|billed\s*to|customer|client)\s*[:\-]\s*([^\r\n]{2,160})",),
    )
    invoice_date_raw = _find_value(
        text,
        (r"(?:invoice\s*date|date\s*issued|issued\s*on)\s*[:\-]\s*([A-Za-z0-9,/\- ]{6,24})",),
    )
    due_date_raw = _find_value(text, (r"(?:due\s*date|payment\s*due)\s*[:\-]\s*([A-Za-z0-9,/\- ]{6,24})",))

    total_raw = _find_value(
        text,
        (r"(?:amount\s*due|total\s*due|grand\s*total|invoice\s*total|total)\s*[:\-]?\s*(?:[$€£₹]\s*)?([0-9][0-9,]*(?:\.\d{1,2})?)",),
    )
    amount = ""
    if total_raw:
        try:
            amount = f"{float(total_raw.replace(',', '')):.2f}"
        except ValueError:
            amount = ""

    fields = {
        "invoice_number": invoice_number,
        "customer_name": customer_name[:160],
        "invoice_date": _normalise_date(invoice_date_raw),
        "due_date": _normalise_date(due_date_raw),
        "amount": amount,
    }
    missing_fields = [key for key, value in fields.items() if not value]
    return fields, missing_fields
