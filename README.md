# Buscript: automating child transportation notifications

I built this project around a recurring filing task: preparing notifications for organized bus trips with children and entering them into the Russian traffic police (GIBDD) portal.

A single trip can involve a customer, a carrier, several buses and drivers, a list of children and accompanying adults, route details, and supporting documents. The information often arrives as messages, scans, spreadsheets, and previous forms. Re-entering it by hand in both a DOCX notification and a multi-step web form is slow and easy to get wrong.

## How I started

My first solution was a set of local scripts. Python assembled the notification document from structured rows. A JSON payload held the values needed by the portal. Playwright filled the form, including repeated bus and driver sections, and stopped at the final confirmation so a person could review the result. A batch runner reused an existing browser session to process several payloads in sequence.

That reduced repetitive entry, but it also exposed the limits of a file-based workflow. Each batch needed its own inputs and paths. The browser session carried operational state. It was difficult to see, in one place, which trip was incomplete, which document had been generated, and what happened during submission.

The first version is in [`legacy/`](legacy/README.md), including the OOXML document builder and Playwright form adapter.

## What I built next

Buscript is the application I started building once the scripts needed shared state and a way to track each trip:

1. A user sends a request and attachments. The API creates a persistent trip draft, then extracts candidate fields from the source material.
2. Missing or uncertain details return as questions. The extracted data is validated against typed schemas and business rules before it can become a portal payload.
3. After confirmation, the document service builds a DOCX, inserts the current carrier's signature assets, converts it to PDF, and stores the generated files separately from the original attachments.
4. A queued browser worker fills the portal and records the result. In the default safe mode it stops before the final submit action.
5. Trip status, generated files, and browser evidence remain tied to the same trip record. Company-owned data is scoped to that company in the API.

The code lives in [`apps/api/`](apps/api/) (FastAPI, Celery, SQLAlchemy, Playwright) and [`apps/web/`](apps/web/) (Next.js). [`docs/portfolio-architecture.md`](docs/portfolio-architecture.md) walks through the main implementation choices.

## The parts that took work

**Documents with changing numbers of people and vehicles.** This was not a simple mail merge. The document builder has to rebuild DOCX tables for each trip, handle route text that spans multiple lines, insert the right carrier signature, and catch placeholders that were not filled. The implementation is in [`documents.py`](apps/api/app/automation/documents.py).

**A stateful portal form.** The GIBDD form moves through several pages and creates additional fields for each bus and driver. The Playwright worker waits for the expected step, fills the repeated sections, uploads documents, and saves evidence when the flow stops or fails. The adapter is in [`submission.py`](apps/api/app/automation/submission.py).

**Requests that do not arrive as clean data.** The extraction step reads messages and attachments, but it does not treat the model's output as a completed filing. Missing times and approximate routes go back to the user for confirmation before the payload is built. That logic is in [`workflow.py`](apps/api/app/automation/workflow.py) and [`extraction.py`](apps/api/app/automation/extraction.py).

## Run it locally

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD and SECRET_KEY in .env.
# Add a Gemini key for extraction, or use LLM_PROVIDER=mock for an offline UI check.
docker compose up --build
```

The UI is at `http://localhost:3100`. Keep `GIBDD_LIVE_SUBMISSION=false` for local exploration.

To check the code:

```bash
cd apps/api && pytest
cd ../.. && npm test
```
