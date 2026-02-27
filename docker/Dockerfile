# Build arguments
ARG PYTHON_VERSION=3.13
ARG ENVIRONMENT=production

# Base image
FROM python:${PYTHON_VERSION}-slim AS base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Builder stage
FROM base AS builder

# Copy requirements
COPY docker/requirements.txt .

# Upgrade build tools to fix known vulnerabilities
RUN pip install --upgrade pip setuptools wheel

# Install Python dependencies
RUN pip install --user --no-warn-script-location -r requirements.txt

# Final stage
FROM base AS final

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Copy Python dependencies from builder
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Copy application code
COPY --chown=appuser:appuser app/ /app/app/

# Copy Alembic migration files (alembic.iniはmigrations配下に含まれる)
COPY --chown=appuser:appuser migrations/ /app/migrations/

# Set PATH to include user's local bin
ENV PATH=/home/appuser/.local/bin:$PATH

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
