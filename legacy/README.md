# The first, file-based version

These are the modules I used before building Buscript. They show the document builder, portal adapter, and batch runner as separate steps.

- [`build_notification_xml.py`](build_notification_xml.py) assembles a DOCX by editing its OOXML parts and rebuilding repeated tables.
- [`gibdd_autofill_node.js`](gibdd_autofill_node.js) fills the portal's multi-step form with Playwright.
- [`gibdd_autofill_chrome_batch.js`](gibdd_autofill_chrome_batch.js) processes several payloads through an existing CDP browser session and leaves final confirmation to the operator.

The application code that replaced this file-based process is in [`apps/api/app/automation/`](../apps/api/app/automation/).
