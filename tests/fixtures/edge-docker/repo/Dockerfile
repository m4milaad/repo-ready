FROM python:3.12.4-slim AS runtime
WORKDIR /app
COPY . .
CMD ["python", "-m", "http.server", "8080"]
