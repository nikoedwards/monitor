# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend (Vite -> dist/) ----
FROM node:20-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---- Stage 2: python runtime serving API + built SPA ----
FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOST=0.0.0.0 \
    PORT=8790

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "playwright>=1.50,<2" \
    && python -m playwright install --with-deps chromium

COPY server ./server
COPY --from=frontend /app/dist ./dist

# Persist SQLite DB + snapshots here (mount a Railway volume at /app/data).
RUN mkdir -p /app/data

EXPOSE 8790
CMD ["python", "-m", "server.app"]
