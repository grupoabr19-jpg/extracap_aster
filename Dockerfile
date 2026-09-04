FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "1800", "web_app:app"]
