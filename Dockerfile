FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY database ./database
COPY wsgi.py .

RUN useradd --create-home appuser && chown -R appuser /app/database
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT} --threads=4 wsgi:app"]
