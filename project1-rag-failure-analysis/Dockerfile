FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# ANTHROPIC_API_KEY is injected at runtime, never baked into the image
ENV RAG_MODEL=claude-haiku-4-5-20251001
EXPOSE 8000
HEALTHCHECK --interval=30s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
