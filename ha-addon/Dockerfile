FROM ghcr.io/home-assistant/base-python:3.12-alpine3.21

RUN apk add --no-cache \
    tesseract-ocr \
    poppler-utils

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
