FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

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

# Create virtual environment in /opt/venv (isolated from /app/kaiburr)
RUN python3 -m venv /opt/venv && \
    pip install --no-cache-dir --upgrade pip

# Pre-install core requirements
COPY requirements.txt /app/kaiburr/
RUN pip install --no-cache-dir -r /app/kaiburr/requirements.txt

# Copy Kaiburr codebase
COPY . /app/kaiburr

# Symlink /opt/venv to /app/kaiburr/venv so kaiburr.sh finds venv/bin/activate
RUN ln -sfn /opt/venv /app/kaiburr/venv

# Install all plugin requirements into the virtual environment
RUN python3 -c "import os, subprocess; \
    [subprocess.run(['pip', 'install', '--no-cache-dir', '-r', os.path.join(r, 'requirements.txt')]) \
     for r, _, f in os.walk('plugins') if 'requirements.txt' in f]"

# Ensure scripts are executable
RUN chmod +x /app/kaiburr/kaiburr.sh

# Ports:
# 29070/udp - JKA / MBII Dedicated Server
# 29070/tcp - RCON
# 6767/tcp  - Kaiburr WebAdmin portal
EXPOSE 29070/udp 29070/tcp 6767/tcp

CMD ["/app/kaiburr/kaiburr.sh"]