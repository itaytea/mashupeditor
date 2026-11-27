# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY requirements.txt .
# CPU-only torch wheels; PyPI is still used for everything else.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MASHUP_DATA_DIR=/data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rubberband-cli libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
RUN useradd --create-home --uid 1000 mashup \
    && mkdir -p /data /app && chown mashup:mashup /data /app
WORKDIR /app
COPY --chown=mashup:mashup app ./app
USER mashup
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
