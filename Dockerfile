# Phoneware hosted Bandwidth MCP (streamable-http, bearer-gated) for Cloud Run.
FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    BW_MCP_TRANSPORT=streamable-http \
    BW_MCP_HOST=0.0.0.0 \
    BW_MCP_PORT=8080

# NOTE: we do NOT `pip install .` — the upstream pyproject `py-modules` omits some
# src modules (e.g. `urls`), so the installed package fails to import. Upstream
# runs from the src/ tree directly (`python src/app.py`); we do the same: install
# the pinned deps, then run from the complete src/ dir via PYTHONPATH.
RUN pip install \
      "fastmcp~=3.2" \
      "mcp~=1.24" \
      "httpx~=0.28.0" \
      "pyyaml~=6.0.0" \
      "werkzeug>=3.1.4" \
      "uvicorn"

COPY src ./src
COPY serve.py ./

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8080
CMD ["python", "serve.py"]
