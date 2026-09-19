FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency definition files
COPY pyproject.toml uv.lock ./

# Install dependencies using frozen lockfile
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code and frontend
COPY app/ app/
COPY frontend/ frontend/

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000

EXPOSE 8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
