"""Start the fictional agency portal with uv run python scripts/portal.py."""

import uvicorn

uvicorn.run("services.portal.main:app", host="127.0.0.1", port=8001, access_log=False)
