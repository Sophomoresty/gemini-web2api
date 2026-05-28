FROM python:3.12-slim

WORKDIR /app

COPY gemini_web2api.py .
COPY config.example.json ./config.json

EXPOSE 8081

CMD ["python", "gemini_web2api.py"]
