FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
# 1 worker + 16 threads: atende várias requisições lentas em paralelo
CMD ["gunicorn", "-w", "1", "--threads", "16", "-b", "0.0.0.0:5000", "app:app"]
