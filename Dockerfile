FROM python:3.12-slim

WORKDIR /app
COPY . /app/supportbot
ENV PYTHONPATH=/app
RUN python -m compileall -q /app/supportbot

EXPOSE 8080
CMD ["python", "-m", "supportbot"]
