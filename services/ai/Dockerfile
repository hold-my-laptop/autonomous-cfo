# OCR models (PP-OCRv6 small, Apache-2.0) taken from the onnxocr wheel on PyPI; only the
# two .onnx files reach the final image.
FROM registry.access.redhat.com/ubi9/python-312:latest AS ocr-models
WORKDIR /opt/app-root/src
COPY --chown=1001:0 scripts/fetch_ocr_models.py scripts/
RUN pip install --no-cache-dir onnx==1.23.2 && \
    python scripts/fetch_ocr_models.py --out /opt/app-root/src/models/ocr


FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root/src

COPY --chown=1001:0 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps rapidocr_onnxruntime==1.2.3

COPY --chown=1001:0 . .
COPY --chown=1001:0 --from=ocr-models /opt/app-root/src/models/ocr models/ocr

USER 1001
ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    OCR_MODEL_DIR=/opt/app-root/src/models/ocr
EXPOSE 8080

CMD ["uvicorn", "ai.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8080"]
