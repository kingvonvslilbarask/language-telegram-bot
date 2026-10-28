FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY assets ./assets
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser
# Mount a volume at /app/data so the delivery ledger survives restarts.
CMD ["python", "-m", "bot.main"]
