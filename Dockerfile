FROM python:3.11-slim
WORKDIR /app
COPY shim/server.py /app/server.py
# data dir holds responses.json (editable) + requests.jsonl (the capture)
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data \
    PORT=8080
EXPOSE 8080
CMD ["python", "/app/server.py"]
