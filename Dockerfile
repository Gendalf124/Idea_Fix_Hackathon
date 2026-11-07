FROM python:3.12-slim

# Логи сразу в консоль, без .pyc, без кэша pip; база по умолчанию лежит в томе /data
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/svodka.db

WORKDIR /app

# Сначала зависимости: пока requirements.txt не менялся, этот слой берётся из кэша
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Бот работает не от root; /data — папка для базы
RUN useradd --create-home bot && mkdir -p /data && chown bot:bot /data
USER bot
VOLUME /data

CMD ["python", "main.py"]