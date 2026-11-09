FROM python:3.12-slim

# Логи сразу в консоль
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/svodka.db

WORKDIR /app


COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Бот работает не от root; /data — папка для базы
RUN useradd --create-home bot && mkdir -p /data && chown bot:bot /data
USER bot
VOLUME /data

CMD ["python", "main.py"]