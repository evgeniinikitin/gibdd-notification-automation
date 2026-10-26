# Local development

## First run

```bash
cp .env.example .env
docker compose up --build
```

Open:

- product UI: `http://localhost:3100`
- API/OpenAPI: `http://127.0.0.1:8000/docs`

The internal administrator is disabled by default. To enable it, set `BOOTSTRAP_ADMIN_ENABLED=true` and provide a unique `BOOTSTRAP_ADMIN_PASSWORD` in `.env`. Set `POSTGRES_PASSWORD` and `SECRET_KEY` before starting Docker Compose.

## Assistant extraction

For LLM extraction, set a supported provider and its key:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
```

Every user message or attachment persists a workflow run and starts extraction
without Redis. Poll `GET /api/v1/conversations/{conversation_id}/workflow`.
When a key is missing, the run ends as `config_required`; add the key, restart
the API, then call
`POST /api/v1/conversations/{conversation_id}/workflow/retry`.

An explicit network-free smoke mode is available only outside production:

```env
LLM_PROVIDER=mock
```

It parses labeled input and is never selected automatically.

## Safe portal testing

Keep:

```env
GIBDD_LIVE_SUBMISSION=false
GIBDD_HEADFUL=false
```

Docker has no desktop display, so its browser worker is headless. It populates
the portal and saves confirmation PNG/HTML artifacts, but the final submit
control cannot be activated. To inspect the managed browser directly, run the
browser worker explicitly on a local desktop with `GIBDD_HEADFUL=true`;
`portal_url` alone does not transfer the Playwright session to another browser.
Server deployment uses the isolated headless runtime; live submission is
enabled only by a production secret after acceptance.
