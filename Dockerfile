FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# Data directory for SQLite (mount as volume for persistence)
RUN mkdir -p /data

VOLUME ["/data"]
EXPOSE 8080

ENV DATABASE_PATH=/data/reports.db

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
