"""Quick retrieval tester — run queries against the corpus and inspect results.

Usage:
    python -m src.test_retrieval "how do I set up a DJI Dock 3"
    python -m src.test_retrieval "verkos detection events" --ia-filter device-management
    python -m src.test_retrieval "mission scheduler" --top-k 5
"""

import argparse

from sentence_transformers import SentenceTransformer

from src.config import SETTINGS
from src.tools.corpus_store import search, get_stats

_model = None

def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(SETTINGS["pinecone"]["embedding_model"])
    return _model


def embed_query(text: str) -> list[float]:
    """Embed a single query text using local model."""
    model = get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def test_query(query: str, top_k: int = 10, ia_filter: str | None = None):
    """Run a query and print results."""
    print(f"\nQuery: {query}")
    print(f"Top-K: {top_k}")
    if ia_filter:
        print(f"IA filter: {ia_filter}")
    print("-" * 60)

    # Embed query
    vector = embed_query(query)

    # Build filter
    filter_dict = None
    if ia_filter:
        filter_dict = {"ia_node": {"$eq": ia_filter}}

    # Search
    results = search(vector, top_k=top_k, filter_dict=filter_dict)

    if not results:
        print("No results found.")
        return

    for i, result in enumerate(results, 1):
        meta = result["metadata"]
        content_preview = meta.get("content", "")[:200]
        print(f"\n--- Result {i} (score: {result['score']:.4f}) ---")
        print(f"  IA Node:  {meta.get('ia_node')} ({meta.get('ia_label', '')})")
        print(f"  Source:   {meta.get('source_url', 'N/A')}")
        print(f"  Type:     {meta.get('source_type', 'N/A')}")
        print(f"  Heading:  {meta.get('heading', 'N/A')}")
        print(f"  Tags:     {meta.get('feature_tags', [])}")
        print(f"  Content:  {content_preview}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test retrieval against Pinecone corpus")
    parser.add_argument("query", help="Natural language query")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--ia-filter", type=str, default=None, help="Filter by IA node ID")
    args = parser.parse_args()

    # Show index stats first
    stats = get_stats()
    print(f"Index has {stats.total_vector_count} vectors")

    test_query(args.query, top_k=args.top_k, ia_filter=args.ia_filter)
