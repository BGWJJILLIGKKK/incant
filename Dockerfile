# syntax=docker/dockerfile:1
# incant in one image: Python + dependencies, ffmpeg, headless Chromium (JavaScript web pages),
# faster-whisper (speech-to-text QA) and Kokoro (CPU voices), with their models baked in.
# Not included: the GPU services (LLM, IndexTTS) — the container reaches them over HTTP like any client.
#
#   docker build -t incant .                                   # everything (~3.3 GB)
#   docker build -t incant:lean --build-arg BAKE_MODELS=0 .    # models download on first use into /data
#   docker build -t incant --build-arg CONVERTERS=pandoc .    # + docx/odt/rtf/fb2/html (calibre: + Kindle)
#   docker build -t incant_local --build-arg EXTRAS="qa browser kokoro anthropic nlp mobi torch" .
#                                       # + PyTorch (CPU) and Docling: OCR for scanned PDFs (compose: incant_local)
#   docker build -t incant:min --build-arg EXTRAS="" .         # no browser, QA or Kokoro (HTTP TTS only, ~1.1 GB)
#
#   docker compose run --rm incant books/b.epub     # data directory from .env (INCANT_DATA=…)
#   docker run --rm -it --network host --user "$(id -u):$(id -g)" -v /srv/audiobooks:/data incant books/b.epub
#
# /data holds books, work/, output/ and your incant.local.toml (settings, secrets, cookies paths).

ARG PYTHON=3.12
ARG EXTRAS="qa browser kokoro anthropic nlp mobi"

# ---------------------------------------------------------------- build: the virtualenv
FROM python:${PYTHON}-slim-bookworm AS build
ARG EXTRAS
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON}-slim-bookworm
ARG EXTRAS
ARG BAKE_MODELS=1
ARG CONVERTERS=""
ENV PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    HF_HOME=/opt/models/hf \
    INCANT_KOKORO_DIR=/opt/models/kokoro \
    INCANT_DOCLING_DIR=/opt/models/docling \
    INCANT_HOME=/app \
    INCANT_DATA=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv

# E-book converters for ingest=ebook (docx/odt/rtf/fb2/html: pandoc; also Kindle formats: calibre, large).
RUN if [ -n "$CONVERTERS" ]; then apt-get update \
      && apt-get install -y --no-install-recommends $CONVERTERS && rm -rf /var/lib/apt/lists/*; fi

# Chromium's headless shell and the system libraries it needs (the browser extra only).
RUN case " $EXTRAS " in *" browser "*) playwright install --with-deps --only-shell chromium \
      && rm -rf /var/lib/apt/lists/* ;; esac

# Models: baked in (works offline, nothing to download per container), or fetched on first use.
# set -e and plain `;` sequencing: any failed download fails the build (an `a && b` list wouldn't).
RUN set -e; mkdir -p /opt/models/hf /opt/models/kokoro; \
    if [ "$BAKE_MODELS" = "1" ]; then \
      case " $EXTRAS " in *" qa "*) \
        python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8')" ;; esac; \
      case " $EXTRAS " in *" torch "*) \
        docling-tools models download -o /opt/models/docling layout rapidocr ;; esac; \
      case " $EXTRAS " in *" kokoro "*) for f in kokoro-v1.0.onnx voices-v1.0.bin; do \
        python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
          "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f" "/opt/models/kokoro/$f"; \
        done ;; esac; \
    fi; \
    chmod -R a+rwX /opt/models   # readable by any --user, and writable for first-use downloads

WORKDIR /app
COPY incant.toml lexicon.toml LICENSE THIRD_PARTY_NOTICES.md ./
COPY LICENSES ./LICENSES
COPY voices ./voices
LABEL org.opencontainers.image.title="incant" \
      org.opencontainers.image.description="Ingest Novels, Cast Actors, Narrate Tales: multi-voice audiobooks" \
      org.opencontainers.image.source="https://github.com/BGWJJILLIGKKK/incant" \
      org.opencontainers.image.documentation="https://github.com/BGWJJILLIGKKK/incant/blob/main/THIRD_PARTY_NOTICES.md"

# Run as your own uid (--user, or compose's user:) so files written to /data stay yours.
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app /data
USER app
WORKDIR /data
VOLUME /data
EXPOSE 8765
ENTRYPOINT ["python", "-m", "incant"]
CMD ["--help"]
