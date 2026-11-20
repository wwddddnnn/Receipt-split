FROM node:22-slim AS web-build

WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system receipt && adduser --system --ingroup receipt --home /app receipt

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web-build /web/dist ./static

RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown -R receipt:receipt /app /data

USER receipt

EXPOSE 8080

CMD ["uvicorn", "receipt_split.web:app", "--host", "0.0.0.0", "--port", "8080"]
