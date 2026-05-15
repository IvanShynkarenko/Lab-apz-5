FROM python:3.10-slim
WORKDIR /app
COPY proto/ /app/proto/
COPY logging-service/ /app/logging-service/
RUN pip install --no-cache-dir -r /app/logging-service/requirements.txt
ENV PYTHONPATH=/app
CMD ["python", "/app/logging-service/main.py"]
