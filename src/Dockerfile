FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools + Node.js (for LiteParse PDF parser)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install LiteParse globally (PDF parsing with spatial layout preservation)
RUN npm install -g @llamaindex/liteparse

# Python deps
COPY pyproject.toml ./pyproject.toml
COPY src ./src

RUN pip install .

# Two services can run from this image:
#   MCP server → 8081 (primary tool interface)
#   Chainlit UI → 8000 (analyst interface)
EXPOSE 8000 8081

## Data and output directories are bind-mounted via docker-compose.yml.
## Do NOT declare VOLUME here — it creates anonymous volumes that shadow
## the bind mounts and make the container see empty directories.

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD python -c "import os,socket; port=int(os.getenv('PORT','8081')); s=socket.create_connection(('127.0.0.1',port),timeout=3); s.close()"

# Default: MCP server
CMD ["sh", "-c", "python -m src.services.mcp_http"]
