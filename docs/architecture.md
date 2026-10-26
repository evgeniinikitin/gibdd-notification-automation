# Buscript architecture

Buscript is a multi-tenant operations platform. A tenant is displayed as a company in the UI and owns exactly one active carrier profile.

Public registration creates a tenant, its first `client_admin` user, and an
empty notification balance in one database transaction. The bootstrapped
`admin` role belongs to a hidden internal tenant and is used only for the
administration console; cross-tenant reads and balance changes are unavailable
to company roles.

The public repository generates a synthetic, unsigned notification template on first run.
Buscript stores one application-wide copy; it is not a tenant-owned profile
setting. A carrier signature is never embedded in the base template;
generation may apply the current tenant profile asset later.

## Runtime boundaries

```text
browser
  └─ web (Next.js)
       └─ api (FastAPI)
            ├─ PostgreSQL
            ├─ Redis
            ├─ storage
            ├─ worker
            ├─ scheduler
            └─ browser-worker ──direct──> GIBDD

llm provider ──scoped HTTP client/proxy──> OpenRouter
```

The browser worker must never inherit a global proxy. OpenRouter proxy configuration belongs only to the provider HTTP client.

## Safety invariants

- Tenant identity comes from the authenticated server-side principal.
- Every client-owned object is tenant scoped.
- Empty API collections remain empty in the UI; demo fallbacks are forbidden.
- User uploads are source attachments and can never be labelled as generated
  notifications or receipts.
- The Files workspace lists only system-generated notification artifacts,
  grouped by their trip.
- Every trip receives an immutable server-side reference in the form
  `BS-YYYYMM-XXXX`; the UI never derives business identifiers from UUIDs.
- Balance history resolves its actor from the persisted user record.
- Original workspace automation is read-only.
- Normal startup never creates a demo customer; every company appears through
  registration or an explicit `DEMO_SEED_ENABLED=true` fixture.
- Carrier data always comes from the tenant profile, never an uploaded request.
- Missing end time always creates a clarification; `18:00` is merely a suggestion.
- An approximate route remains unconfirmed until the user approves it.
- `GIBDD_LIVE_SUBMISSION=false` prevents the irreversible final portal action.
- A dry run returns quota reservations instead of consuming them.
- OpenRouter and portal credentials never enter source control or application logs.
