FROM python:3.11.11-slim

WORKDIR app
COPY . .
RUN python -m unittest discover --start-directory tests

CMD ["python", "-m", "example_app"]
