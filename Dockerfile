FROM python:3.13-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ynab_balance_monitor.py .
CMD ["python", "-u", "ynab_balance_monitor.py"]
