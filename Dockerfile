FROM node:22-bookworm-slim AS pi-sdk
WORKDIR /app
RUN npm install --prefix /app --no-save --ignore-scripts \
       @earendil-works/pi-coding-agent@1.5.2 @earendil-works/pi-ai@1.5.2 typebox \
    && node --input-type=module -e "import {createAgentSession,ModelRuntime,SessionManager,DefaultResourceLoader,SettingsManager,defineTool} from '@earendil-works/pi-coding-agent'; if(!createAgentSession||!ModelRuntime.create||!SessionManager.continueRecent||!DefaultResourceLoader||!SettingsManager.inMemory||!defineTool) process.exit(1)"

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright DATA_ROOT=/data PORT=8080
ENV PI_HOST_PORT=8090 PI_HOST_BIND=0.0.0.0 PROJECTS_ROOT=/data/projects \
    OPENAI_OAUTH_LOCAL_HOME=/auth-local SPECWEAVE_PI_SAFE_RESOURCES_DIR=/app/pi-resources
WORKDIR /app
# Playwright bundles a Node binary which requires libatomic.so.1.
# Install this BEFORE invoking `python -m playwright install --with-deps chromium`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && useradd -r -u 10001 -m -d /home/specweave specweave \
    && mkdir -p /data/projects /auth-local /app/pi-resources \
    && chown -R specweave:specweave /ms-playwright /data /app /auth-local \
    && chmod 0700 /auth-local
COPY --from=pi-sdk /usr/local/bin/node /usr/local/bin/node
COPY --from=pi-sdk /app/node_modules /app/node_modules
COPY --chown=specweave:specweave source/ /app/source/
COPY --chown=specweave:specweave migrations/ /app/migrations/
COPY --chown=specweave:specweave alembic.ini entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
USER specweave
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=4s --start-period=30s --retries=12 \
    CMD node -e "Promise.all([process.env.PORT,process.env.PI_HOST_PORT].map(p=>fetch('http://127.0.0.1:'+p+'/health').then(r=>{if(!r.ok)process.exit(1)}))).catch(()=>process.exit(1))"
CMD ["/app/entrypoint.sh"]
