"""Download the local embedding model into LECTA_MODEL_DIR (image build time)."""

import os

from fastembed import TextEmbedding

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
cache = os.environ.get("LECTA_MODEL_DIR", "/opt/models")
model = TextEmbedding(MODEL, cache_dir=cache, threads=1)
vec = next(iter(model.embed(["ciao mondo"])))
print(f"downloaded {MODEL}: dim={len(vec)}")
