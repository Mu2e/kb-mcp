# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY README.md .
COPY src/ ./src/

# Install dependencies (server, kb, and GCP extras for Firestore support)
RUN pip install --no-cache-dir -e ".[server,kb,gcp]"

# Create data directory for API keys
RUN mkdir -p /app/data

# For HTTPS in Docker, uncomment these lines and mount/copy your certificates:
# COPY certs/cert.pem /app/certs/cert.pem
# COPY certs/key.pem /app/certs/key.pem
# ENV USE_HTTPS=true

# Default configuration (for HTTPS termination before container)
ENV PORT=8443
ENV HOST=0.0.0.0
ENV USE_HTTPS=false

# Run the server
CMD ["test-mcp"]
