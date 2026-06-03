FROM python:3.12-slim

WORKDIR /app

# Prevents Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system deps (needed by some ccxt/cryptography deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Ensure runtime directories exist inside the image
RUN mkdir -p data logs static/css static/js

EXPOSE 8000

# Single worker is correct — the app uses asyncio internally;
# multiple workers would create separate Telethon sessions.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "asyncio", \
     "--log-level", "warning"]
