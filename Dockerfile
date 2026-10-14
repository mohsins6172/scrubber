# Production image: Python app served by gunicorn, with ExifTool and qpdf installed.
FROM python:3.12-slim

# System tools the cleaner calls: ExifTool for images and PDFs, qpdf for PDF rewriting.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl qpdf \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py scrub.py ./
COPY templates ./templates
COPY static ./static

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# 2 workers and 4 threads each. The timeout allows for large files.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", \
     "--timeout", "180", "--access-logfile", "-", "app:app"]
