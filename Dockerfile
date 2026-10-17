FROM python:3.11-slim

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY . .
RUN python -m pip install --no-cache-dir --no-build-isolation .

CMD ["make", "test"]
