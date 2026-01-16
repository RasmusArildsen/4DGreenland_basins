FROM osgeo/grass-gis:8.4.2-debian

# System utilities + venv support (PEP 668 compatible)
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip \
      python3-venv \
      ca-certificates \
      tini \
    && rm -rf /var/lib/apt/lists/*

# Create venv for Jupyter + Python deps
ENV VENV=/opt/venv
RUN python3 -m venv ${VENV} \
 && ${VENV}/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
 && ${VENV}/bin/pip install --no-cache-dir \
      jupyterlab ipykernel numpy pandas matplotlib

# Ensure venv is the default python/pip/jupyter
ENV PATH="${VENV}/bin:${PATH}"

# Parameterized UID/GID for local bind-mount friendliness
# Defaults are reasonable for many Linux distros.
ARG NB_UID=1000
ARG NB_GID=1000

# Create a jovyan user/group matching the host IDs (passed via build args)
RUN groupadd -g ${NB_GID} jovyan \
 && useradd -m -s /bin/bash -u ${NB_UID} -g ${NB_GID} jovyan

# Allow runtime pip installs into the venv (non-persistent unless you persist /opt/venv)
RUN chown -R jovyan:jovyan /opt/venv

USER jovyan
WORKDIR /home/jovyan

# Copy example notebooks (optional)
COPY --chown=jovyan:jovyan *.ipynb /home/jovyan/

EXPOSE 8888
ENTRYPOINT ["tini","--"]
CMD ["jupyter","lab","--ip=0.0.0.0","--port=8888","--no-browser","--NotebookApp.token="]