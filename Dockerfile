FROM osgeo/grass-gis:8.4.2-debian


# System utilities you may want; keep minimal
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip \
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

# Optional: non-root user (recommended for Jupyter)
RUN useradd -ms /bin/bash jovyan

# Allow users to make pip installation to the container
# Note: This is only added to the container, and is therefore not persistant
RUN chown -R jovyan:jovyan /opt/venv

USER jovyan
WORKDIR /home/jovyan

COPY *.ipynb /home/jovyan/

EXPOSE 8888
ENTRYPOINT ["tini","--"]
CMD ["jupyter","lab","--ip=0.0.0.0","--port=8888","--no-browser","--NotebookApp.token="]
