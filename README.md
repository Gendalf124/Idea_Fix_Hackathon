# Сферум.Сводка — бот для MAX



## Запуск в Docker
1. Скопируйте `.env.example` в `.env` и впишите свои `BOT_TOKEN` и `AI_API_KEY`.
2. Соберите образ: `docker build -t svodka-bot .`
3. Запустите: `docker run --rm --env-file .env svodka-bot`

## Запуск без Docker
```
pip install -r requirements.txt
python main.py
```

## Команды бота
- `/start` — приветствие
- `/gpt <вопрос>` — ответ ИИ