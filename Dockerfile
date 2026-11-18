FROM python:3.12-slim
# OCR for Russian, Uzbek Latin and English document samples. OCR remains best-effort.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-uzb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY rules ./rules
COPY config ./config
COPY web ./web
RUN pip install --no-cache-dir ".[ingest]"
ENV LAGRANGE_DATA_DIR=/data LAGRANGE_WEB_DIR=/app/web
VOLUME /data
EXPOSE 8000
# The API is password-protected (HTTP Basic, user "lagrange"). Binding beyond loopback is refused unless
# LAGRANGE_AUTH_PASSWORD is set:
#   docker run -e LAGRANGE_AUTH_PASSWORD=... -p 127.0.0.1:8000:8000 lagrange-rx   (put TLS in front for public use)
CMD ["lagrange-rx", "serve", "--host", "0.0.0.0", "--allow-non-loopback", "--port", "8000"]
