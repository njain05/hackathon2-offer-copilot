# Offer Copilot: dashboard + both Telegram bots in one container.
# Secrets (TELEGRAM_BOT_TOKEN, SELLER_BOT_TOKEN, SELLER_CODE, ANTHROPIC_API_KEY) come from the host's settings, never the image.
FROM python:3.14-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "-m", "src.serve"]
