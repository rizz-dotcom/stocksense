# Container build for cloud hosting (Alternate Clouds "GitHub Repo", Railway, Render, a VM...).
# Secrets and the database are never baked into the image: give them as environment
# variables / secrets in the hosting dashboard, and mount permanent storage at /app/data.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8501 \
    STOCKSENSE_ALLOW_WEB_SETUP=false \
    STOCKSENSE_DB_PATH=/app/data/stocksense.db \
    STOCKSENSE_LOG_PATH=/app/data/stocksense.log

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/_stcore/health' % os.environ.get('PORT','8501'), timeout=4)"

# The platform may assign the port through $PORT; inside a container the server must
# listen on all interfaces (the platform's HTTPS proxy sits in front of it).
CMD ["sh", "-c", "exec streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true"]
