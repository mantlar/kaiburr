FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app/kaiburr

# Install system utilities + 32-bit libraries required by OpenJK/mbiided.i386
RUN dpkg --add-architecture i386 && \
    apt-get update && apt-get install -y --no-install-recommends \
        bash \
        curl \
        git \
        sqlite3 \
        build-essential \
        libc6:i386 \
        libstdc++6:i386 \
        zlib1g:i386 \
    && rm -rf /var/lib/apt/lists/*

# Copy core requirements and install them into a container virtual environment
COPY requirements.txt ./
RUN python3 -m venv /app/kaiburr/venv && \
    /app/kaiburr/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /app/kaiburr/venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy Kaiburr codebase
COPY . /app/kaiburr

# Discover and install all plugin requirements into the virtual environment
RUN python3 -c "import os, subprocess; \
    venv_pip = '/app/kaiburr/venv/bin/pip'; \
    [subprocess.run([venv_pip, 'install', '--no-cache-dir', '-r', os.path.join(r, 'requirements.txt')]) \
     for r, _, f in os.walk('plugins') if 'requirements.txt' in f]"

# Ensure scripts are executable
RUN chmod +x /app/kaiburr/kaiburr.sh

# Ports:
# 29070/udp - JKA / MBII Dedicated Server
# 29070/tcp - RCON
# 6767/tcp  - Kaiburr WebAdmin portal
EXPOSE 29070/udp 29070/tcp 6767/tcp

CMD ["/app/kaiburr/kaiburr.sh"]