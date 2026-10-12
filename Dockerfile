FROM python:3.12-slim

# Railway provides PORT via environment at runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Long polling bot plus a tiny health endpoint on $PORT.
CMD ["python", "bot.py"]
