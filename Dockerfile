# Pin the multi-platform base manifest for reproducible clean-room builds.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY . .
RUN python -m pip install --no-cache-dir --no-build-isolation .
ENV REPO_READY_TEST_NO_SRC=1

CMD ["python", "-m", "pytest", "-q"]
