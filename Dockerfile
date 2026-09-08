# Runs the whole thing: API, UI, and a knowledge layer already built from the
# starter documents, so the site opens with something to look at.
FROM python:3.11-slim

# Hugging Face Spaces runs the container as uid 1000; other hosts do not care.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --chown=app:app pyproject.toml ./
COPY --chown=app:app src ./src
RUN pip install --no-cache-dir --disable-pip-version-check .

# The prepared corpus. Shipped compressed because SQLite holds the page text of
# every document, and unpacked at build time so a restart is always a clean copy.
COPY --chown=app:app deploy/factlayer.db.gz ./
RUN gunzip factlayer.db.gz && chown app:app factlayer.db

# WORKDIR ran as root, so /app itself is root-owned even though every file placed in
# it was individually chowned. SQLite needs to create a same-directory -journal file
# on every write, and a non-owner cannot create files in a directory it does not own
# — that surfaces as "attempt to write a readonly database" on the first upload, not
# at build time, because nothing writes to the corpus until then.
RUN chown app:app /app

USER app
ENV FACTLAYER_DB=/app/factlayer.db \
    FACTLAYER_UPLOADS=/tmp/uploads \
    FACTLAYER_RPM=14 \
    FACTLAYER_WORKERS=4 \
    PYTHONUNBUFFERED=1

# The host names the port it will send traffic to, and they do not agree: Spaces
# expects 7860, Render and most others inject PORT. Read it rather than assume it.
EXPOSE 7860
CMD ["sh", "-c", "uvicorn factlayer.api:app --host 0.0.0.0 --port ${PORT:-7860}"]
