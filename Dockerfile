FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates cron \
    libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libpangocairo-1.0-0 libgtk-3-0 libx11-xcb1 libxcb-dri3-0 \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir playwright && playwright install chromium

COPY scraper.py .
COPY scraper_browser.py .
COPY api.py .
COPY templates/ templates/
COPY crontab /etc/cron.d/silver-tracker

# set permissions and register crontab
RUN chmod 0644 /etc/cron.d/silver-tracker \
    && crontab /etc/cron.d/silver-tracker

RUN mkdir -p /data
ENV DB_PATH=/data/prices.db

EXPOSE 8080

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--access-logfile", "-", "api:app"]
