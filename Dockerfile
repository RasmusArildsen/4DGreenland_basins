FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GISBASE=/usr/lib/grass84 \
    GRASS_CONFIG_DIR=/root/.grass8 \
    GRASS_ADDON_BASE=/root/.grass8/addons

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    grass \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    libgeos-dev \
    libspatialindex-dev \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip3 install --break-system-packages -r requirements.txt

COPY . /app

ENTRYPOINT ["python3", "run_ensemble.py"]
CMD ["--help"]
