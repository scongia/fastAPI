FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    fastapi \
    "uvicorn[standard]" \
    python-multipart

COPY parsers/ parsers/
COPY pdf_to_ofx.py pdf_unlock.py ./
COPY api/ api/

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
