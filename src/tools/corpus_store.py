"""Pinecone corpus store — handles index creation, upsert, and search."""

from pinecone import Pinecone, ServerlessSpec

from src.config import PINECONE_API_KEY, SETTINGS


def get_client() -> Pinecone:
    """Return an authenticated Pinecone client."""
    return Pinecone(api_key=PINECONE_API_KEY)


def ensure_index() -> str:
    """Create the index if it doesn't exist. Returns the index name."""
    pc = get_client()
    cfg = SETTINGS["pinecone"]
    index_name = cfg["index_name"]

    existing = [idx.name for idx in pc.list_indexes()]
    if index_name not in existing:
        pc.create_index(
            name=index_name,
            dimension=cfg["embedding_dimension"],
            metric=cfg["metric"],
            spec=ServerlessSpec(
                cloud=cfg["cloud"],
                region=cfg["region"],
            ),
        )
        print(f"Created index '{index_name}'")
    else:
        print(f"Index '{index_name}' already exists")

    return index_name


def get_index():
    """Return a ready-to-use Index object."""
    pc = get_client()
    index_name = SETTINGS["pinecone"]["index_name"]
    return pc.Index(index_name)


def upsert_chunks(chunks: list[dict], namespace: str = "") -> int:
    """Upsert a batch of chunks into Pinecone.

    Each chunk dict must have:
        - id: str
        - values: list[float]  (embedding vector)
        - metadata: dict       (content, ia_node, source_type, etc.)

    Returns the number of vectors upserted.
    """
    index = get_index()
    vectors = [
        {
            "id": chunk["id"],
            "values": chunk["values"],
            "metadata": chunk["metadata"],
        }
        for chunk in chunks
    ]
    # Pinecone recommends batches of 100
    batch_size = 100
    total = 0
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i : i + batch_size]
        index.upsert(vectors=batch, namespace=namespace)
        total += len(batch)
    return total


def search(
    query_vector: list[float],
    top_k: int = 10,
    filter_dict: dict | None = None,
    namespace: str = "",
) -> list[dict]:
    """Query the index and return top_k results with metadata."""
    index = get_index()
    results = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True,
        filter=filter_dict,
        namespace=namespace,
    )
    return [
        {
            "id": match.id,
            "score": match.score,
            "metadata": match.metadata,
        }
        for match in results.matches
    ]


def get_stats() -> dict:
    """Return index stats (total vector count, namespaces, etc.)."""
    index = get_index()
    return index.describe_index_stats()
