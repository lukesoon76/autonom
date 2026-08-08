# Autonom — Streamlit food-recommendation app for Render (Docker web service).
# The embedding model and a prebuilt vector store are baked into the image so the
# container starts fast; at runtime start.sh symlinks the writable state (vector
# store, member accounts, uploads) onto the Render persistent disk so it survives
# redeploys.
FROM python:3.12-slim

# system libs some wheels want (lxml for trafilatura, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    AUTONOM_DATA_DIR=/var/data

# python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code + bundled curated workbook
COPY . .

# bake the embedding model into the image (no runtime download)
RUN python -c "from sentence_transformers import SentenceTransformer as S; \
S('paraphrase-multilingual-MiniLM-L12-v2')"

# build the vector store at image-build time, then stash it as the seed copy
# (start.sh copies it onto the empty persistent disk on first boot)
RUN python curate_authority.py --csv config/curated_authority.csv \
    && python import_eatlist.py --workbook data/Master_List.xlsx \
    && mv chroma_db _seed_chroma

RUN chmod +x start.sh
EXPOSE 8501
CMD ["bash", "start.sh"]
