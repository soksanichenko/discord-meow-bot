# Stage 1: Build frontend (skipped if frontend/package.json is absent)
FROM node:22-alpine AS frontend-builder
WORKDIR /app
COPY frontend/ ./
RUN if [ -f package.json ]; then npm ci; fi
ARG VITE_DISCORD_CLIENT_ID
RUN if [ -f package.json ]; then npm run build; else mkdir -p dist; fi

# Stage 2: Build Python venv
FROM almalinux:10 AS python-builder
RUN dnf install -y python3 python3-devel gcc && dnf clean all
WORKDIR /code
COPY sources/requirements.txt sources/requirements.txt
RUN python3 -m venv venv && \
    venv/bin/pip install --upgrade pip && \
    venv/bin/pip install -r sources/requirements.txt

# Stage 3: Runtime
FROM almalinux:10
RUN dnf install -y epel-release && \
    dnf install -y python3 ffmpeg-free && \
    dnf clean all
ENV PYTHONPATH="."
ENV KVIZGAME_FRONTEND_DIR="/code/frontend"
WORKDIR /code
COPY --from=python-builder /code/venv ./venv
COPY sources/ ./sources/
COPY --from=frontend-builder /app/dist/ ./frontend/
ENTRYPOINT ["bash"]
