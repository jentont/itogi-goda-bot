# Бот «Итоги года»: Python-бот + веб-сервер мини-аппа в одном процессе.
# Явный Dockerfile, чтобы хостинг не принял webapp/app.js за Node.js-проект.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=3000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 3000
CMD ["python", "main.py"]
