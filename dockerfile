# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt


# Copy application files
COPY launch.py .
COPY view.html .
COPY script.js .

# Copy sessions directory if exists (for build context)
RUN mkdir -p sessions

# Create downloads directory
RUN mkdir -p downloads

# Create volume mount points
VOLUME ["/app/downloads", "/app/sessions"]

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "launch:app", "--host", "0.0.0.0", "--port", "8000"]
