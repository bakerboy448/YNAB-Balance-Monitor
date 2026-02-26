FROM python:3.13-alpine

LABEL org.opencontainers.image.title="YNAB-Balance-Monitor" \
      org.opencontainers.image.description="Projects minimum checking account balance and alerts via Apprise" \
      org.opencontainers.image.source="https://github.com/bakerboy448/YNAB-Balance-Monitor" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    adduser -D -h /app monitor

COPY ynab_balance_monitor.py .

USER monitor

CMD ["python", "-u", "ynab_balance_monitor.py"]
