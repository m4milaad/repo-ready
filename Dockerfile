FROM python:3.12-slim

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY . .
RUN python -m pip install --no-cache-dir --no-build-isolation .
ENV REPO_READY_TEST_NO_SRC=1

CMD ["python", "-m", "pytest", "-q"]
