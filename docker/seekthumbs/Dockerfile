# Dex Hub seek preview container (see README.md in this folder).
FROM python:3.12-slim-bookworm

# Debian's ffmpeg has zscale (libzimg), used to tone-map HDR frames
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /nonexistent seekthumbs \
    && mkdir /cache && chown seekthumbs /cache

COPY server.py /app/server.py

USER seekthumbs
ENV DEXHUB_THUMBS_CACHE=/cache \
    DEXHUB_THUMBS_PORT=8765 \
    PYTHONUNBUFFERED=1
VOLUME /cache
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).read() == b'ok' else 1)"
CMD ["python3", "/app/server.py"]
