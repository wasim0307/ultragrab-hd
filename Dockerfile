FROM python:3.12-slim

# Install ffmpeg and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Set default port
ENV PORT=5055
EXPOSE 5055

# Run with gunicorn on dynamic cloud PORT
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-5055} app:app --timeout 300"]
