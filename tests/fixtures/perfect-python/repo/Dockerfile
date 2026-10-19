FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN python -m pip install --no-index -r requirements.lock
CMD ["python", "-m", "pytest", "-q"]
