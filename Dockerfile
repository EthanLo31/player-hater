FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir discord.py python-dotenv nhlpy

COPY . /app

CMD ["python", "app.py"]