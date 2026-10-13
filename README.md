# LocalBiz AI

LocalBiz AI is a privacy-conscious invoice and customer-message assistant for freelancers and small businesses. It tracks invoices in a local SQLite database, extracts text from text-based PDF invoices for human review, and can draft customer messages with a local Ollama model.

> **Privacy note:** Local storage and local AI reduce data sharing, but they do not guarantee complete security. Keep your computer protected, use a private local installation for real customer records, and do not publish or share this app with private invoice data. OCR for scanned PDFs is not implemented.

## Problem and open-source value

Small businesses need a simple way to track outstanding invoices and write consistent follow-ups without depending on paid AI APIs. LocalBiz AI keeps invoice records in SQLite and sends message-generation prompts only to the Ollama endpoint you configure. It is open source so people can run it, inspect the code, and adapt it to their own workflow.

## Features

- Dashboard totals calculated from saved invoices, with helpful zero-data states.
- PDF upload capped at 10 MB, text extraction with pypdf, and an editable review step before saving.
- Invoice search, payment-status filters, overdue detection, status updates, details, and deletion.
- Local Ollama message generation for reminders, overdue reminders, confirmations, follow-ups, and thank-you messages.
- Editable message output and copy-to-clipboard action.
- Responsive layouts, CSRF protection on state-changing forms, parameterized SQLite queries, secure generated filenames, and private PDF storage outside the public static directory.

## Technology

- Python 3.10+
- Flask, Jinja, HTML, CSS, and JavaScript
- SQLite
- pypdf
- Ollama with a configurable open-weight model
- pytest

## Run on Windows with PowerShell

1. Extract `LocalBizAI.zip` to a folder. For example:

   ```powershell
   Expand-Archive -Path .\LocalBizAI.zip -DestinationPath .\LocalBizAI
   Set-Location .\LocalBizAI
   ```

2. Open the extracted folder in VS Code:

   ```powershell
   code .
   ```

3. Install Python 3.10 or newer if it is not already installed, then create and activate a virtual environment:

   ```powershell
   py -3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation for this terminal, run:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
   .\.venv\Scripts\Activate.ps1
   ```

4. Install dependencies:

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

5. Set a local session secret and start the app:

   ```powershell
   $env:SESSION_SECRET = [guid]::NewGuid().ToString("N")
   python app.py
   ```

   You can also double-click `run.bat`; it creates a virtual environment and installs the required packages if needed.

6. Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The SQLite database is created in `instance/` on first launch. Private PDF files are saved in `uploads/`.

## Set up local AI with Ollama

Ollama is optional for invoice tracking and PDF extraction. Message generation requires Ollama running locally.

1. Install Ollama from [ollama.com/download](https://ollama.com/download), then open a new PowerShell terminal.
2. Download a compatible model:

   ```powershell
   ollama pull llama3.2:3b
   ```

3. If Ollama is not already running as a background service, start it in a separate terminal:

   ```powershell
   ollama serve
   ```

4. Configure the model before launching the app (the default is `llama3.2:3b`):

   ```powershell
   $env:OLLAMA_BASE_URL = "http://localhost:11434"
   $env:OLLAMA_MODEL = "llama3.2:3b"
   ```

   The app uses Ollama's local HTTP API. It does not send invoice details to an external AI service. If Ollama or its configured model is unavailable, the app reports that no message was generated.

## Run tests

Activate the virtual environment, then run:

```powershell
python -m pytest
```

The test suite uses a temporary SQLite database and does not include real customer records.

## Privacy and security limits

- Invoice PDFs are stored under the private `uploads/` folder, not served as public static files.
- Local SQLite and Ollama do not by themselves guarantee security. Protect the computer, backups, and any shared network.
- This starter app has no user accounts or multi-user access controls. Do not expose it to the public internet with private business data.
- Password-protected PDFs and PDFs without extractable text are not processed. Scanned-image OCR is not included.
- This Replit preview is for demonstration. For confidential business data, run it on your own computer and do not publish the development app.

## Replit preview notes

The preview uses the same Flask routes, SQLite operations, PDF extraction, and forms as the local app. Ollama generation requires an Ollama endpoint reachable by the running app; the default `localhost:11434` refers to the app's own environment, not your laptop. The preview does not share your laptop's Ollama installation.

## Screenshots

Screenshots can be added here after capturing the running application. No user research or customer feedback is claimed.

## Limitations and future improvements

- OCR for scanned PDFs is not included.
- Currency is currently stored as a numeric amount without a per-invoice currency field.
- The app is intended for one local operator; it does not include accounts or shared access.
- Future contributions could add OCR as an opt-in local capability, export tools, and more invoice-field extraction tests.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and contribution guidelines. Run the test suite before submitting changes, and never commit invoice PDFs, database files, or secrets.

## License

MIT. See [LICENSE](LICENSE).
