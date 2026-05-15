FROM python:3.10-slim
WORKDIR /app
COPY proto/ /app/proto/
COPY facade-service/ /app/facade-service/
RUN pip install --no-cache-dir -r /app/facade-service/requirements.txt
ENV PYTHONPATH=/app
CMD ["python", "/app/facade-service/main.py"]
