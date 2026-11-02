FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    DB_PATH=/app/data/bot.db \
    BACKUP_DIR=/app/backups

WORKDIR /app

RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data /app/backups \
    && chown -R bot:bot /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY groupbot ./groupbot
COPY bot.py config.example.json ./

USER bot

EXPOSE 8080

CMD ["python", "-m", "groupbot"]
