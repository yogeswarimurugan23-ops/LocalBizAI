# Contributing to LocalBiz AI

Contributions that improve privacy, accessibility, reliability, documentation, or beginner onboarding are welcome.

## Before you start

1. Fork the repository and create a focused branch.
2. Set up Python 3.10 or newer and install `requirements.txt`.
3. Run the existing tests with `python -m pytest`.
4. Keep invoice documents and local database files out of commits.

## Changes

- Keep local AI local. Do not add paid or external AI services without an explicit product decision.
- Keep database access parameterized and validate user input at route boundaries.
- Add or update tests for changed behavior.
- Describe any privacy or setup trade-offs in the README.

## Pull requests

Include a short description of the change, how you tested it, and any limitations that remain. Do not include real customer data, uploaded invoices, secrets, or user feedback that was not provided.
