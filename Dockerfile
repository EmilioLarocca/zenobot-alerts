# How the bot is packaged. install.sh (or Railway/Fly) builds it automatically.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Railway sets $PORT; 8080 is the default when running elsewhere
EXPOSE 8080
CMD ["python", "main.py"]
