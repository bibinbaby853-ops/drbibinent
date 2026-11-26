# syntax=docker/dockerfile:1
# Dashboard + API in one image. Build from the momentum_intelligence/ directory:  docker compose build
# Optional build secret `extra_ca`: a CA bundle for networks behind a TLS-inspecting proxy. It is used only
# while installing packages and is never copied into the image (docs/DEPLOYMENT.md, "Building behind a proxy").
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MI_FRONTEND_DIST=/srv/frontend/dist
WORKDIR /srv/backend
COPY backend/requirements.txt backend/requirements.lock ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir --root-user-action=ignore -r requirements.txt -c requirements.lock
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini .
COPY --from=web /web/dist /srv/frontend/dist
RUN useradd --create-home --uid 10001 appuser && mkdir -p /srv/data && chown appuser /srv/data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"
# --proxy-headers: the client address seen by the login throttle comes from the HTTPS proxy (FORWARDED_ALLOW_IPS).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-server-header"]
