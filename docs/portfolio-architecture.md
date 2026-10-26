# Implementation notes

## From files to trip records

The original workflow moved data between a request, a DOCX file, a JSON payload, and an open browser tab. Buscript gives each trip a persistent record and a server-generated reference. The first substantive message or attachment creates a draft; later steps update that same trip instead of relying on a folder name or a browser tab as the source of truth.

The web client is in [`apps/web`](../apps/web/). The API and workers are in [`apps/api`](../apps/api/). PostgreSQL stores trips, users, companies, and job state. Redis carries task IDs for Celery; uploaded documents remain in tenant-scoped storage.

## Intake and validation

[`workflow.py`](../apps/api/app/automation/workflow.py) coordinates the conversation around a trip. [`extraction.py`](../apps/api/app/automation/extraction.py) defines the structured response expected from a model. The response is parsed into typed fields, checked against business rules, and sent back for clarification when required information is missing or approximate.

The LLM is useful for reading inconsistent inputs, especially attachments. It does not decide whether the portal's final submit button may be pressed. Carrier details come from the authenticated company's profile, not from an untrusted customer attachment.

## Documents

[`documents.py`](../apps/api/app/automation/documents.py) edits DOCX as an OOXML package. It rebuilds variable-length tables for children, companions, buses, and drivers, replaces route text and other placeholders, adds the current company's signature assets, checks for unresolved markers, and converts the result to PDF.

[`demo_template.py`](../apps/api/app/demo_template.py) builds the template used by the local demo at startup.

## Portal worker and safe mode

[`submission.py`](../apps/api/app/automation/submission.py) contains the Playwright adapter for the multi-step portal. [`orchestration.py`](../apps/api/app/automation/orchestration.py) connects validation, document generation, quota reservation, and the browser job. Browser evidence is stored with the trip rather than mixed into the ordinary file archive.

The default `GIBDD_LIVE_SUBMISSION=false` stops before the final portal action and returns the dry-run reservation. Enabling live submission requires a separate operational decision and an environment that can reach the portal.
