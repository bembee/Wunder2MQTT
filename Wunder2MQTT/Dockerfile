FROM python:3.11-alpine

RUN apk add --no-cache curl ca-certificates

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py /app/main.py

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://127.0.0.1:80/health || exit 1

CMD ["python", "main.py"]
