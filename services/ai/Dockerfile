FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root/src

COPY --chown=1001:0 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1001:0 . .

USER 1001
ENV PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
EXPOSE 8080

CMD ["uvicorn", "ai.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8080"]
