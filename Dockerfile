# Stage 1: Build Angular frontend
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + static frontend
FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Copy built Angular files from stage 1
COPY --from=frontend-build /app/frontend/dist/browser /app/static

# Data directory for SQLite (mount as volume for persistence)
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080

ENV DATABASE_PATH=/data/reports.db

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
