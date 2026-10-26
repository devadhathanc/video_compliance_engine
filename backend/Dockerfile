# Multi-stage Dockerfile for Brand Guardian AI Backend
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg for video handling if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project files
COPY pyproject.toml ./
COPY README.md ./

# Install python dependencies
RUN uv pip install --system -r pyproject.toml

# Copy backend source code
COPY backend ./backend
COPY main.py ./

# Expose FastAPI port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

# Run FastAPI server
CMD ["uvicorn", "backend.src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
