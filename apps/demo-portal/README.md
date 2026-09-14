# Fictional Demo Borough portal

The functional ASGI application lives in `services/portal/main.py` so the local server and Lambda adapter use one implementation. Start it through the project `npm run dev` supervisor. For an independently configured process: `uv run uvicorn services.portal.main:app --host 127.0.0.1 --port 8001 --no-access-log`.

Only an authenticated server can issue a short-lived transfer grant. The form checks the exact recipient, category, description, coordinates, contacts and attachment hashes. Each attempt has a deterministic conditional ticket key. Receipt URLs include a private opaque capability; management APIs require the service secret and development-only scenario operations reject AWS/production.

Local records are SQLite plus private image files. AWS uses the configured private DynamoDB portal table and S3 `portal/` prefix. This portal is fictional and intentionally carries no city branding. It has no free-form admin account or real municipal integration.
