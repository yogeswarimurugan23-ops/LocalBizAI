"""LocalBiz AI — a privacy-conscious invoice and message assistant."""

from __future__ import annotations

import hmac
import os
import re
import secrets
import shutil
import sqlite3
import uuid
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from services.ollama_service import OllamaUnavailable, generate_customer_message
from services.pdf_service import extract_invoice_fields, read_pdf_text

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PAGE_SIZE = 10
VALID_STATUSES = {"paid", "pending"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    invoice_date TEXT,
    due_date TEXT,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('paid', 'pending')),
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invoices_due_date ON invoices(due_date);
"""


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SESSION_SECRET") or secrets.token_hex(32),
        DATABASE=os.environ.get("LOCALBIZ_DATABASE", str(Path(app.instance_path) / "localbiz.sqlite")),
        UPLOAD_FOLDER=os.environ.get("LOCALBIZ_UPLOADS", str(ROOT / "uploads")),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 2,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    init_db(app)

    @app.before_request
    def protect_state_changes() -> None:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token") or request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="Your form has expired. Refresh the page and try again.")

    @app.context_processor
    def provide_csrf_token() -> dict[str, Any]:
        def csrf_token() -> str:
            token = session.get("_csrf_token")
            if not token:
                token = secrets.token_urlsafe(32)
                session["_csrf_token"] = token
            return token

        return {"csrf_token": csrf_token}

    @app.get("/")
    def dashboard():
        db = get_db()
        stats_row = db.execute(
            """
            SELECT
                COUNT(*) AS total_invoices,
                COALESCE(SUM(amount), 0) AS total_amount,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN amount ELSE 0 END), 0) AS pending_amount,
                COALESCE(SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END), 0) AS paid_amount,
                COALESCE(SUM(CASE WHEN status = 'pending' AND date(due_date) < date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS overdue_count
            FROM invoices
            """
        ).fetchone()
        pending_count = db.execute(
            "SELECT COUNT(*) FROM invoices WHERE status = 'pending' AND (due_date IS NULL OR date(due_date) >= date('now', 'localtime'))"
        ).fetchone()[0]
        paid_count = db.execute("SELECT COUNT(*) FROM invoices WHERE status = 'paid'").fetchone()[0]
        stats = {
            "total_invoices": stats_row["total_invoices"],
            "total_amount": stats_row["total_amount"],
            "pending_amount": stats_row["pending_amount"],
            "paid_amount": stats_row["paid_amount"],
            "overdue_count": stats_row["overdue_count"],
            "invoice_count": stats_row["total_invoices"],
            "outstanding_amount": stats_row["pending_amount"],
            "pending_count": pending_count,
            "paid_count": paid_count,
        }
        status_rows = db.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid,
                SUM(CASE WHEN status = 'pending' AND date(due_date) < date('now', 'localtime') THEN 1 ELSE 0 END) AS overdue,
                SUM(CASE WHEN status = 'pending' AND date(due_date) >= date('now', 'localtime') THEN 1 ELSE 0 END) AS pending
            FROM invoices
            """
        ).fetchone()
        recent_invoices = db.execute(
            """
            SELECT *, CASE
                WHEN status = 'pending' AND due_date IS NOT NULL AND date(due_date) < date('now', 'localtime') THEN 1
                ELSE 0
            END AS is_overdue
            FROM invoices ORDER BY created_at DESC, id DESC LIMIT 6
            """
        ).fetchall()
        return render_template(
            "index.html",
            stats=stats,
            status_counts={key: status_rows[key] or 0 for key in ("paid", "pending", "overdue")},
            recent_invoices=recent_invoices,
        )

    @app.route("/upload", methods=["GET", "POST"])
    def upload_invoice():
        if request.method == "GET":
            return render_template(
                "upload.html",
                fields={},
                missing_fields=[],
                review_file_token="",
                extracted=False,
            )

        if request.form.get("stage") == "review":
            return save_invoice()

        uploaded = request.files.get("invoice_file") or request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("Choose a PDF invoice to continue.", "error")
            return redirect(url_for("upload_invoice"))
        if not uploaded.filename.lower().endswith(".pdf"):
            flash("Only PDF files are supported.", "error")
            return redirect(url_for("upload_invoice"))

        content = uploaded.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            flash("The PDF is larger than the 10 MB upload limit.", "error")
            return redirect(url_for("upload_invoice"))
        if not content.startswith(b"%PDF-"):
            flash("This file does not appear to be a valid PDF.", "error")
            return redirect(url_for("upload_invoice"))

        token = secrets.token_urlsafe(24)
        pending_dir = Path(app.config["UPLOAD_FOLDER"]) / ".pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        for stale_file in pending_dir.iterdir():
            try:
                if stale_file.is_file() and time.time() - stale_file.stat().st_mtime > 24 * 60 * 60:
                    stale_file.unlink(missing_ok=True)
            except OSError:
                app.logger.warning("Could not clean up an expired temporary invoice file")
        pending_pdf = pending_dir / f"{token}.pdf"
        pending_json = pending_dir / f"{token}.json"
        pending_pdf.write_bytes(content)
        try:
            pdf_text = read_pdf_text(pending_pdf)
        except ValueError as error:
            pending_pdf.unlink(missing_ok=True)
            flash(str(error), "error")
            return redirect(url_for("upload_invoice"))
        except Exception:
            pending_pdf.unlink(missing_ok=True)
            flash("The PDF could not be read. It may be damaged or use an unsupported format.", "error")
            return redirect(url_for("upload_invoice"))

        fields, missing_fields = extract_invoice_fields(pdf_text)
        pending_json.write_text("review", encoding="utf-8")
        session["review_file_token"] = token
        return render_template(
            "upload.html",
            fields=fields,
            missing_fields=missing_fields,
            review_file_token=token,
            extracted=True,
            extraction_warning=(
                "This PDF has no extractable text. It may be a scanned image; OCR is not included."
                if not pdf_text.strip()
                else ""
            ),
        )

    @app.post("/upload/save")
    def save_invoice():
        token = request.form.get("review_file_token", "")
        if not token or token != session.get("review_file_token") or not re.fullmatch(r"[A-Za-z0-9_-]{20,60}", token):
            flash("The upload review expired. Upload the invoice again.", "error")
            return redirect(url_for("upload_invoice"))

        pending_dir = Path(app.config["UPLOAD_FOLDER"]) / ".pending"
        pending_pdf = pending_dir / f"{token}.pdf"
        pending_json = pending_dir / f"{token}.json"
        if not pending_pdf.is_file() or not pending_json.is_file():
            session.pop("review_file_token", None)
            flash("The reviewed PDF is no longer available. Upload it again.", "error")
            return redirect(url_for("upload_invoice"))

        values, errors = validate_invoice_form(request.form)
        if errors:
            flash("Please correct the highlighted invoice details.", "error")
            return render_template(
                "upload.html",
                fields=values,
                missing_fields=[key for key in errors],
                review_file_token=token,
                extracted=True,
                field_errors=errors,
            ), 400

        saved_name = f"{uuid.uuid4().hex}.pdf"
        saved_path = Path(app.config["UPLOAD_FOLDER"]) / secure_filename(saved_name)
        try:
            db = get_db()
            db.execute(
                """
                INSERT INTO invoices
                    (invoice_number, customer_name, invoice_date, due_date, amount, status, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["invoice_number"],
                    values["customer_name"],
                    values["invoice_date"],
                    values["due_date"],
                    float(Decimal(values["amount"])),
                    values["status"],
                    str(saved_path),
                ),
            )
            shutil.move(str(pending_pdf), saved_path)
            db.commit()
        except sqlite3.IntegrityError:
            get_db().rollback()
            if saved_path.exists() and not pending_pdf.exists():
                shutil.move(str(saved_path), pending_pdf)
            flash("That invoice number is already saved. Check the number and try again.", "error")
            return render_template(
                "upload.html",
                fields=values,
                missing_fields=["invoice_number"],
                review_file_token=token,
                extracted=True,
            ), 400
        except Exception:
            get_db().rollback()
            if saved_path.exists() and not pending_pdf.exists():
                shutil.move(str(saved_path), pending_pdf)
            app.logger.exception("Unable to save invoice")
            flash("The invoice could not be saved. Your details have not been added.", "error")
            return render_template(
                "upload.html",
                fields=values,
                missing_fields=[],
                review_file_token=token,
                extracted=True,
            ), 500

        pending_json.unlink(missing_ok=True)
        session.pop("review_file_token", None)
        flash(f"Invoice {values['invoice_number']} was saved for payment tracking.", "success")
        return redirect(url_for("invoice_detail", invoice_id=get_db().execute("SELECT last_insert_rowid()").fetchone()[0]))

    @app.get("/invoices")
    def invoice_history():
        search = request.args.get("search", request.args.get("q", "")).strip()[:100]
        status_filter = request.args.get("status", "all")
        if status_filter not in {"all", "paid", "pending", "overdue"}:
            status_filter = "all"
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            page = 1

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            clauses.append("(invoice_number LIKE ? COLLATE NOCASE OR customer_name LIKE ? COLLATE NOCASE)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        if status_filter == "paid":
            clauses.append("status = 'paid'")
        elif status_filter == "pending":
            clauses.append("status = 'pending' AND date(due_date) >= date('now', 'localtime')")
        elif status_filter == "overdue":
            clauses.append("status = 'pending' AND date(due_date) < date('now', 'localtime')")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        db = get_db()
        count = db.execute(f"SELECT COUNT(*) FROM invoices {where_sql}", params).fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        invoices = db.execute(
            f"""
            SELECT *, CASE
                WHEN status = 'pending' AND date(due_date) < date('now', 'localtime') THEN 1
                ELSE 0
            END AS is_overdue
            FROM invoices {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        return render_template(
            "history.html",
            invoices=invoices,
            search=search,
            status_filter=status_filter,
            page=page,
            pages=pages,
            total_count=count,
        )

    @app.get("/invoices/<int:invoice_id>")
    def invoice_detail(invoice_id: int):
        invoice = get_db().execute(
            """
            SELECT *, CASE
                WHEN status = 'pending' AND date(due_date) < date('now', 'localtime') THEN 1
                ELSE 0
            END AS is_overdue
            FROM invoices WHERE id = ?
            """,
            (invoice_id,),
        ).fetchone()
        if invoice is None:
            abort(404)
        return render_template("invoice_detail.html", invoice=invoice, is_overdue=bool(invoice["is_overdue"]))

    @app.post("/invoices/<int:invoice_id>/status")
    def update_invoice_status(invoice_id: int):
        status = request.form.get("status", "")
        if status not in VALID_STATUSES:
            flash("Choose a valid payment status.", "error")
            return redirect(url_for("invoice_detail", invoice_id=invoice_id))
        cursor = get_db().execute("UPDATE invoices SET status = ? WHERE id = ?", (status, invoice_id))
        get_db().commit()
        if cursor.rowcount == 0:
            abort(404)
        flash("Payment status updated.", "success")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    @app.post("/invoices/<int:invoice_id>/delete")
    def delete_invoice(invoice_id: int):
        db = get_db()
        invoice = db.execute("SELECT file_path FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if invoice is None:
            abort(404)
        db.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        db.commit()
        Path(invoice["file_path"]).unlink(missing_ok=True)
        flash("Invoice and its private PDF were deleted.", "success")
        return redirect(url_for("invoice_history"))

    @app.route("/messages", methods=["GET", "POST"])
    def message_generator():
        form_data = {
            "customer_name": "",
            "invoice_number": "",
            "amount": "",
            "due_date": "",
            "message_type": "payment_reminder",
            "tone": "polite",
            "instructions": "",
        }
        generated_message = ""
        ollama_error = ""
        if request.method == "POST":
            form_data.update({key: request.form.get(key, "").strip() for key in form_data})
            valid_form = validate_message_form(form_data)
            if not valid_form:
                ollama_error = "Please provide a valid customer name, invoice number, and positive amount."
            else:
                try:
                    generated_message = generate_customer_message(
                        form_data,
                        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                        model=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
                    )
                except OllamaUnavailable as error:
                    ollama_error = str(error)
        return render_template(
            "message_generator.html",
            generated_message=generated_message,
            ollama_error=ollama_error,
            form_data=form_data,
        )

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="We couldn't find that page or invoice."), 404

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", "The form could not be validated. Refresh the page and try again.")
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        flash("The PDF is larger than the 10 MB upload limit.", "error")
        return redirect(url_for("upload_invoice"))

    @app.errorhandler(500)
    def internal_error(_error):
        return render_template("error.html", code=500, message="Something went wrong. No invoice was marked as saved."), 500

    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"])
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g.db = db
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    with app.app_context():
        db = sqlite3.connect(app.config["DATABASE"])
        db.executescript(SCHEMA)
        db.commit()
        db.close()
    app.teardown_appcontext(close_db)


def validate_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def validate_invoice_form(form) -> tuple[dict[str, str], dict[str, str]]:
    values = {
        "invoice_number": form.get("invoice_number", "").strip()[:80],
        "customer_name": form.get("customer_name", "").strip()[:160],
        "invoice_date": form.get("invoice_date", "").strip(),
        "due_date": form.get("due_date", "").strip(),
        "amount": form.get("amount", "").strip(),
        "status": form.get("status", "pending").strip(),
    }
    errors: dict[str, str] = {}
    for key, label in (
        ("invoice_number", "Invoice number"),
        ("customer_name", "Customer name"),
        ("amount", "Amount"),
    ):
        if not values[key]:
            errors[key] = f"{label} is required."
    if values["status"] not in VALID_STATUSES:
        errors["status"] = "Choose a valid payment status."
    for key in ("invoice_date", "due_date"):
        if values[key] and not validate_iso_date(values[key]):
            errors[key] = "Use a valid date."
    if values["amount"]:
        try:
            amount = Decimal(values["amount"])
            if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
                errors["amount"] = "Enter an amount greater than 0 and below 1,000,000,000."
            else:
                values["amount"] = f"{amount:.2f}"
        except InvalidOperation:
            errors["amount"] = "Enter a valid number."
    return values, errors


def validate_message_form(values: dict[str, str]) -> bool:
    if not values["customer_name"] or not values["invoice_number"]:
        return False
    if values["message_type"] not in {
        "payment_reminder",
        "overdue_reminder",
        "payment_confirmation",
        "invoice_follow_up",
        "thank_you",
    }:
        return False
    if values["tone"] not in {"polite", "friendly", "formal"}:
        return False
    try:
        amount = Decimal(values["amount"])
        return amount.is_finite() and amount > 0 and amount <= Decimal("999999999.99")
    except InvalidOperation:
        return False


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
