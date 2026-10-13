from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import app as localbiz


@pytest.fixture()
def application(tmp_path: Path):
    application = localbiz.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(tmp_path / "instance" / "test.sqlite"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "MAX_CONTENT_LENGTH": 1024 * 1024,
        }
    )
    yield application


@pytest.fixture()
def client(application):
    return application.test_client()


def csrf(client) -> str:
    with client.session_transaction() as state:
        state["_csrf_token"] = "test-csrf-token"
    return "test-csrf-token"


def insert_invoice(application, *, invoice_number="INV-100", status="pending", due_date="2099-01-01", amount=125.5):
    with application.app_context():
        db = localbiz.get_db()
        db.execute(
            """
            INSERT INTO invoices
                (invoice_number, customer_name, invoice_date, due_date, amount, status, file_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (invoice_number, "Northwind Studio", "2026-10-01", due_date, amount, status, str(Path(application.config["UPLOAD_FOLDER"]) / "invoice.pdf")),
        )
        db.commit()
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def make_text_invoice_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    lines = [
        "Invoice Number: INV-77",
        "Bill To: Northwind Studio",
        "Invoice Date: 2026-10-01",
        "Due Date: 2026-11-01",
        "Amount Due: $325.50",
    ]
    content = "BT /F1 12 Tf 50 700 Td " + " T* ".join(f"({line}) Tj" for line in lines) + " ET"
    stream = DecodedStreamObject()
    stream.set_data(content.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_application_startup_and_database_creation(application):
    assert Path(application.config["DATABASE"]).is_file()
    with application.app_context():
        tables = localbiz.get_db().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'invoices'"
        ).fetchone()
    assert tables is not None


def test_dashboard_loads_with_zero_statistics_when_empty(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"0" in response.data
    assert b"Northwind Studio" not in response.data


def test_invoice_history_loads_and_searches(client, application):
    insert_invoice(application)
    response = client.get("/invoices?q=Northwind")
    assert response.status_code == 200
    assert b"INV-100" in response.data
    assert client.get("/invoices?q=missing").status_code == 200


def test_message_generator_page_loads(client):
    assert client.get("/messages").status_code == 200


def test_pdf_upload_validation_rejects_non_pdf(client):
    token = csrf(client)
    response = client.post(
        "/upload",
        data={"_csrf_token": token, "invoice_file": (BytesIO(bytes([1, 2, 3])), "invoice.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Only PDF files are supported" in response.data


def test_pdf_upload_validation_rejects_invalid_pdf_signature(client):
    token = csrf(client)
    response = client.post(
        "/upload",
        data={"_csrf_token": token, "invoice_file": (BytesIO(b"not a pdf"), "invoice.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"does not appear to be a valid PDF" in response.data


def test_text_pdf_fields_are_extracted_for_review_before_saving(client):
    response = client.post(
        "/upload",
        data={"csrf_token": csrf(client), "file": (BytesIO(make_text_invoice_pdf()), "invoice.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"Review invoice" in response.data
    assert b"INV-77" in response.data
    assert b"Northwind Studio" in response.data
    assert b"325.50" in response.data
    assert b"Review this PDF" not in response.data


def test_saving_reviewed_invoice_persists_record(client, application):
    token = csrf(client)
    review_token = "abcdefghijklmnopqrstuvwxyz123456"
    pending = Path(application.config["UPLOAD_FOLDER"]) / ".pending"
    pending.mkdir(parents=True)
    (pending / f"{review_token}.pdf").write_bytes(b"%PDF-test")
    (pending / f"{review_token}.json").write_text(json.dumps({}), encoding="utf-8")
    with client.session_transaction() as state:
        state["review_file_token"] = review_token

    response = client.post(
        "/upload/save",
        data={
            "_csrf_token": token,
            "review_file_token": review_token,
            "invoice_number": "INV-101",
            "customer_name": "Northwind Studio",
            "invoice_date": "2026-10-01",
            "due_date": "2026-10-31",
            "amount": "275.50",
        },
    )
    assert response.status_code == 302
    with application.app_context():
        invoice = localbiz.get_db().execute(
            "SELECT invoice_number, amount, status, file_path FROM invoices WHERE invoice_number = ?",
            ("INV-101",),
        ).fetchone()
    assert invoice["invoice_number"] == "INV-101"
    assert invoice["amount"] == pytest.approx(275.50)
    assert invoice["status"] == "pending"
    assert Path(invoice["file_path"]).is_file()


def test_payment_status_can_be_updated(client, application):
    invoice_id = insert_invoice(application)
    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"_csrf_token": csrf(client), "status": "paid"},
    )
    assert response.status_code == 302
    with application.app_context():
        status = localbiz.get_db().execute(
            "SELECT status FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()["status"]
    assert status == "paid"


def test_dashboard_totals_follow_database_records(client, application):
    insert_invoice(application, invoice_number="INV-PAID", status="paid", amount=30.25)
    insert_invoice(application, invoice_number="INV-OVERDUE", status="pending", due_date="2020-01-01", amount=19.75)
    response = client.get("/")
    assert response.status_code == 200
    assert b"50.00" in response.data
    assert b"19.75" in response.data
    assert b"INV-PAID" in response.data or b"INV-OVERDUE" in response.data


def test_ollama_unavailable_is_reported_without_faking_generation(client, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise localbiz.OllamaUnavailable("Ollama is unavailable. No AI message was generated.")

    monkeypatch.setattr(localbiz, "generate_customer_message", unavailable)
    response = client.post(
        "/messages",
        data={
            "_csrf_token": csrf(client),
            "customer_name": "Northwind Studio",
            "invoice_number": "INV-102",
            "amount": "35.00",
            "due_date": "2026-11-01",
            "message_type": "payment_reminder",
            "tone": "polite",
            "instructions": "",
        },
    )
    assert response.status_code == 200
    assert b"Ollama is unavailable" in response.data
    assert b"No AI message was generated" in response.data
    assert not re.search(rb"Here is your message", response.data)


def test_state_changes_require_csrf_token(client, application):
    invoice_id = insert_invoice(application)
    assert client.post(f"/invoices/{invoice_id}/status", data={"status": "paid"}).status_code == 400


def test_invoice_can_be_deleted_with_its_private_file(client, application):
    invoice_id = insert_invoice(application)
    with application.app_context():
        file_path = Path(
            localbiz.get_db().execute("SELECT file_path FROM invoices WHERE id = ?", (invoice_id,)).fetchone()["file_path"]
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"private test content")
    response = client.post(
        f"/invoices/{invoice_id}/delete",
        data={"csrf_token": csrf(client)},
    )
    assert response.status_code == 302
    assert not file_path.exists()
    with application.app_context():
        assert localbiz.get_db().execute("SELECT 1 FROM invoices WHERE id = ?", (invoice_id,)).fetchone() is None
