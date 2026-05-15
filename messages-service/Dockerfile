FROM python:3.10-slim
WORKDIR /app
COPY messages-service/ /app/messages-service/
RUN pip install --no-cache-dir -r /app/messages-service/requirements.txt
CMD ["python", "/app/messages-service/main.py"]
