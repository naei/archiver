FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 archiver \
    && useradd --uid 10001 --gid archiver --create-home archiver \
    && mkdir /data && chown archiver:archiver /data
COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY archiver ./archiver
COPY web ./web
COPY run.py .
USER archiver
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=4)"
CMD ["python", "run.py"]
