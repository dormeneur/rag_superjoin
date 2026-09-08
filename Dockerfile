# Runs the whole thing: API, UI, and a knowledge layer already built from the
# starter documents, so the site opens with something to look at.
FROM python:3.11-slim

# Hugging Face Spaces runs the container as uid 1000.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app src ./src
RUN pip install --no-cache-dir --disable-pip-version-check .

# The prepared corpus. Shipped compressed because SQLite holds the page text of
# every document, and unpacked at build time so a restart is always a clean copy.
COPY --chown=app:app deploy/factlayer.db.gz ./
RUN gunzip factlayer.db.gz && chown app:app factlayer.db

USER app
ENV FACTLAYER_DB=/app/factlayer.db \
    FACTLAYER_UPLOADS=/tmp/uploads \
    FACTLAYER_RPM=14 \
    FACTLAYER_WORKERS=4 \
    PYTHONUNBUFFERED=1

EXPOSE 7860
CMD ["uvicorn", "factlayer.api:app", "--host", "0.0.0.0", "--port", "7860"]
