# Local command agent (robot-command hook). Stdlib-only Python.
FROM python:3.11-slim
WORKDIR /app
COPY command_server.py /app/command_server.py
EXPOSE 8090
CMD ["python", "/app/command_server.py", "8090"]
