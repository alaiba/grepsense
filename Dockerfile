# grepsense Python image — runs the embedder and the MCP server.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY grepsense ./grepsense
RUN pip install --no-cache-dir .

# Pre-cache the embedding model so the first semantic query is fast and the
# container works offline. (This pulls torch + the model — the image is large.)
ENV HF_HOME=/models
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

ENTRYPOINT ["grepsense"]
CMD ["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "8765"]
