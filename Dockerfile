FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ARG THEYARD_VERSION=dev
ENV THEYARD_VERSION=${THEYARD_VERSION}

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["sh", "-c", "mkdir -p /app/data && python seed.py && exec gunicorn -b 0.0.0.0:5000 -w 2 --timeout 120 wsgi:app"]
