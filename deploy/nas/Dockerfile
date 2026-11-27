FROM debian:bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
      /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
      /etc/apt/sources.list 2>/dev/null || true; \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl libgomp1 nginx procps python3 python3-pip \
      python3-venv supervisor tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    rm -f /etc/nginx/sites-enabled/default && \
    python3 -m venv /opt/fridge-venv

COPY server/requirements.txt /tmp/requirements.txt
RUN /opt/fridge-venv/bin/pip install --no-cache-dir --timeout 30 --retries 3 \
      -i https://mirrors.aliyun.com/pypi/simple \
      -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

COPY deploy/nas/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/nas/supervisor.conf /etc/supervisor/conf.d/fridge.conf
COPY deploy/nas/start.sh /usr/local/bin/fridge-start

RUN chmod +x /usr/local/bin/fridge-start && \
    mkdir -p /app/data /app/logs

EXPOSE 9443
CMD ["/usr/local/bin/fridge-start"]
