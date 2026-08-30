# PatchPilot's Python services: the simulated production environment and the
# three MCP servers, plus the orchestrator API.
#
# One image serves all four. They share a dependency set, and the command decides
# which one a container runs — keeping four near-identical Dockerfiles in step
# would be busywork with a real chance of drift.
FROM python:3.12-slim

# git is not optional here: the repository MCP server drives a real clone.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so editing source does not invalidate the install layer.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" \
      "httpx>=0.27" "mcp>=2.0" "python-dotenv>=1.0"

COPY mcp_servers ./mcp_servers
COPY simulator ./simulator
COPY apps/api ./apps/api
COPY agents ./agents
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/apps/api:/app/simulator/checkout-service

# Overridden per service in docker-compose.yml.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
