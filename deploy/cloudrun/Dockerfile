FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --create-home viewer
WORKDIR /app
COPY --chown=viewer:viewer report_viewer.py /app/report_viewer.py
USER viewer

EXPOSE 8080
CMD ["python", "/app/report_viewer.py", "--out-dir", "/reports/out", "--bind", "0.0.0.0", "--port", "8080"]
