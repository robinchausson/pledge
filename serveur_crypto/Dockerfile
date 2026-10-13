FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc python3-dev openssl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY crypto_node.py .
COPY docker-entrypoint.sh .
RUN sed -i -e 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

EXPOSE 8443

ENTRYPOINT ["/app/docker-entrypoint.sh"]