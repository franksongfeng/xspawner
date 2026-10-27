
ARG UBUNTU_TAG=20.04
FROM ubuntu:${UBUNTU_TAG}

# Envirment arguments
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# Change mirror source
RUN cp /etc/apt/sources.list /etc/apt/sources.list.bak \
    &&  sed -i 's/archive.ubuntu.com/mirrors.aliyun.com/g; s/security.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list
# Install fundamental softwares and python
RUN apt-get update && apt-get install -y \
    systemd \
    systemd-sysv \
    python3-pip \
    python3-dev \
    python3-venv \
    curl \
    wget \
    net-tools \
    iputils-ping \
    vim \
    tzdata \
    zip \
    unzip \
    jq \
    openssl \
    libssl-dev \
    openssh-server \
    sqlite3 \
    && ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* /var/tmp/*

# Install caddy
RUN curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
RUN curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
RUN apt-get update
RUN apt-get install -y caddy

# Install python packages
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir \
    tornado==6.4.1 \
    pycurl==7.45.7 \
    pywebio==1.8.3 \
    requests==2.22.0 \
    ipaddress==1.0.23 \
    psutil==5.9.5 \
    bokeh==3.1.1 \
    tortoise-orm==0.23.0 \
    aiomysql==0.2.0 \
    asyncpg==0.30.0 \
    pyinstaller==6.22.3 \
    && python3 -c "import tornado; import pywebio; import pycurl; import requests; import bokeh; import tortoise; print('All Python packages installed successfully)"


ENV HOST=xspawner.com
ENV PORT=8000
ENV CERTFILE=/etc/ssl/certs/xspawner.com.crt
ENV KEYFILE=/etc/ssl/private/xspawner.com.key

# Create self-signed certificate
RUN if [ ! -f ${CERTFILE} ]; then \
        echo "Generate self-signed certificate  for ${HOST}"; \
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout ${KEYFILE} \
            -out ${CERTFILE} \
            -subj "/C=CN/ST=Beijing/L=Beijing/O=xspawner/CN=${HOST}" \
            2>/dev/null; \
        chmod 644 ${CERTFILE}; \
        chmod 640 ${KEYFILE}; \
    fi

# Expose port
EXPOSE 22 80 443
EXPOSE 8000-9000

# Create necessary directories and account
RUN mkdir -p \
    /opt/xspawner

# Set working directory
WORKDIR /opt/xspawner

# copy xspawner config and code
COPY ./xspawner ./xspawner
RUN find /opt/xspawner -type f -name "*.py" -exec chmod 644 {} \;

# Start systemed service
CMD exec python3 -u -m xspawner \
    --id xspawner \
    --plugin administrator \
    --host ${HOST} \
    --port ${PORT} \
    --access 0.0.0.0 \
    --log --severity info \
    --ssl \
    --certfile ${CERTFILE} \
    --keyfile ${KEYFILE}