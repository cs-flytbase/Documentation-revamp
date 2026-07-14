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


def get_existing_source_urls(namespace: str = "") -> set[str]:
    """Fetch all unique source_url values already in Pinecone.

    Uses a zero-vector query with a large top_k to retrieve metadata.
    Since Pinecone doesn't support listing all vectors natively on Starter,
    we query with a dummy vector and iterate.
    """
    index = get_index()
    dim = SETTINGS["pinecone"]["embedding_dimension"]
    urls = set()

    # Query with zero vector to get a broad sample — repeat for each source_type
    for source_type in ("doc", "release"):
        try:
            results = index.query(
                vector=[0.0] * dim,
                top_k=10000,
                include_metadata=True,
                filter={"source_type": {"$eq": source_type}},
                namespace=namespace,
            )
            for match in results.matches:
                url = match.metadata.get("source_url", "")
                if url:
                    urls.add(url)
        except Exception as e:
            print(f"  Warning: Could not query existing URLs for {source_type}: {e}")

    return urls


def delete_by_source_url(source_url: str, namespace: str = "") -> int:
    """Delete all vectors with a given source_url.

    Used during incremental refresh to remove stale chunks before re-upserting.
    """
    index = get_index()
    dim = SETTINGS["pinecone"]["embedding_dimension"]

    # Find vectors with this source_url
    results = index.query(
        vector=[0.0] * dim,
        top_k=1000,
        include_metadata=True,
        filter={"source_url": {"$eq": source_url}},
        namespace=namespace,
    )

    if not results.matches:
        return 0

    ids = [m.id for m in results.matches]
    index.delete(ids=ids, namespace=namespace)
    return len(ids)
