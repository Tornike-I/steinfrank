FROM node:22-slim AS lab
WORKDIR /app/monster-lab
COPY monster-lab/package.json monster-lab/package-lock.json ./
RUN npm ci
COPY monster-lab/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app/frankenstein
COPY frankenstein/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY frankenstein/ ./
COPY --from=lab /app/monster-lab/dist /app/monster-lab/dist
ENV FRANK_DATA_DIR=/data/data FRANK_MONSTERS_DIR=/data/monsters
# The volume at /data starts empty; seed it with the repo's monsters without overwriting ones forged on the server.
CMD mkdir -p /data/data /data/monsters && cp -rn monsters/. /data/monsters/ && \
    exec python -m uvicorn frankenstein.demo:app --host 0.0.0.0 --port ${PORT:-8000}
