FROM python:3.12-slim
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
# The API has no authentication. Inside the container it listens on all interfaces so the port can be
# published; publish it to loopback only:  docker run -p 127.0.0.1:8000:8000 lagrange-rx
CMD ["lagrange-rx", "serve", "--host", "0.0.0.0", "--allow-non-loopback", "--port", "8000"]
