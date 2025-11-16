# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

# Set working directory
WORKDIR /app

# Set build-time environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage - smaller runtime image
FROM python:3.11-slim

# Accept UID and GID as build arguments with defaults
ARG USER_ID=1000
ARG GROUP_ID=1000

# Set working directory
WORKDIR /app

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create group and user with specified IDs
RUN groupadd -g ${GROUP_ID} appuser && \
    useradd -m -u ${USER_ID} -g appuser appuser

# Update PATH for the appuser
ENV PATH=/home/appuser/.local/bin:$PATH

# Copy only the installed packages from builder
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Copy application files with correct ownership
COPY --chown=appuser:appuser main.py .
COPY --chown=appuser:appuser app/ ./app/

# Create necessary directories with correct ownership
RUN mkdir -p sessions downloads && \
    chown -R appuser:appuser /app

# Create volume mount points
VOLUME ["/app/downloads", "/app/sessions"]

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 9868

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9868"]