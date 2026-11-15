FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8788 \
    DATA_DIR=/data

WORKDIR /app

# 仅拷贝运行所需文件，镜像极小（纯标准库，无第三方依赖）
COPY main.py ./
COPY app ./app

RUN mkdir -p /data && \
    adduser -D -H -u 10001 appuser && \
    chown -R appuser:appuser /app /data

USER appuser

EXPOSE 8788
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8788')+'/health').read()" || exit 1

CMD ["python", "main.py"]
