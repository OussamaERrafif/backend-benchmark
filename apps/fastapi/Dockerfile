FROM python:3.12.9-slim-bookworm
WORKDIR /app
COPY apps/fastapi/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/fastapi/main.py ./
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8080 --workers ${APP_WORKERS:-1} --no-access-log"]
