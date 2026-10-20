FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KERAS_BACKEND=torch \
    INFERENCE_DEVICE=cpu \
    BUSBRA_PRODUCTION=1 \
    BUSBRA_DATA_DIR=/data \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2

WORKDIR /app

COPY requirements.txt requirements-production.txt ./
# CPU wheels avoid downloading the much larger CUDA runtime on a CPU service.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 torchvision==0.28.0 \
    && pip install -r requirements-production.txt

COPY models/manifest.json ./models/manifest.json
COPY scripts/download_models.py ./scripts/download_models.py
RUN python scripts/download_models.py

COPY cancer_app ./cancer_app
COPY classification_package/config_modele.json ./classification_package/config_modele.json
COPY segmentation_package/selection.json ./segmentation_package/selection.json
COPY app.py wsgi.py gunicorn.conf.py ./
COPY scripts/manage_recovery.py ./scripts/manage_recovery.py

CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
