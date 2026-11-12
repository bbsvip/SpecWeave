FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright DATA_ROOT=/data PORT=8080
WORKDIR /app
# Playwright bundles a Node binary which requires libatomic.so.1.
# Install this BEFORE invoking `python -m playwright install --with-deps chromium`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && useradd -r -u 10001 -m -d /home/specweave specweave \
    && mkdir -p /data/projects \
    && chown -R specweave:specweave /ms-playwright /data /app
COPY --chown=specweave:specweave source/ /app/source/
COPY --chown=specweave:specweave migrations/ /app/migrations/
COPY --chown=specweave:specweave alembic.ini entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
USER specweave
EXPOSE 8080
CMD ["/app/entrypoint.sh"]
