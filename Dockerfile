FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

# One-command local run: migrate then serve over ASGI (SSE-friendly).
CMD ["sh", "-c", "python manage.py migrate --noinput && uvicorn backend.asgi:application --host 0.0.0.0 --port 8000"]

