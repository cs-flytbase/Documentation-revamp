"""Research agent — queries Pinecone corpus, finds related pages, suggests placement.

One-shot agent. Receives PM doc text, returns:
- Target IA node (where the new doc should go)
- List of impacted existing pages
- Relevant content chunks for drafting context
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

from sentence_transformers import SentenceTransformer

from src.agents.base import BaseAgent
from src.config import SETTINGS, PROJECT_ROOT
from src.tools.corpus_store import search

SYSTEM_PROMPT = """You are the Research Agent for the FlytBase documentation pipeline.

Your job: given a PM (Product Management) document describing a new feature or update,
query the documentation corpus and return structured research output.

You must produce a JSON response with exactly this schema:
{
  "target_ia_node": {
    "id": "the IA node ID where the new doc page should be placed",
    "label": "human-readable IA path like 'Device Management > DJI Docks > Settings'",
    "reasoning": "1-2 sentences explaining why this placement makes sense"
  },
  "impacted_pages": [
    {
      "source_url": "URL of an existing page that needs updates",
      "ia_node": "IA node ID of that page",
      "impact_type": "cross-reference | content-update | restructure",
      "description": "what specifically needs to change on this page"
    }
  ],
  "relevant_chunks": [
    {
      "heading": "heading of the chunk",
      "source_url": "where it came from",
      "ia_node": "IA node ID",
      "relevance": "why this chunk matters for the drafting agent"
    }
  ],
  "sub_queries_used": ["list of the sub-queries you generated from the PM doc"],
  "feature_summary": "2-3 sentence summary of the feature from the PM doc"
}

Rules:
- For target_ia_node, pick the MOST SPECIFIC node in the IA that fits. Don't pick a top-level section when a child fits better.
- For impacted_pages, look carefully at BOTH the semantic results AND the product area sweep results. Any page in the same product area that covers related functionality must be listed — especially release notes that group this product area's features together.
- For relevant_chunks, include the top chunks that give the drafting agent useful context about existing content in the same area.
- Return ONLY valid JSON. No markdown, no explanation outside the JSON.
"""


class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="research",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=4096,
        )
        self._embed_model = None

    def _get_embed_model(self) -> SentenceTransformer:
        if self._embed_model is None:
            self._embed_model = SentenceTransformer(SETTINGS["pinecone"]["embedding_model"])
        return self._embed_model

    def _extract_product_area(self, pm_doc: str) -> str:
        """Ask the LLM to extract the product area name from the PM doc."""
        prompt = f"""Read this PM document and return ONLY the primary product or feature area name.
Examples: "Verkos", "Fleet View", "Mission Scheduler", "Device Management", "Flinks", "Payload Controls".
Return a single short phrase, nothing else.

PM Document (first 1500 chars):
{pm_doc[:1500]}"""
        return self.run(prompt).strip().strip('"').strip("'")

    def _generate_sub_queries(self, pm_doc: str, product_area: str) -> list[str]:
        """Generate semantic sub-queries from the PM doc."""
        prompt = f"""Given this PM document about a feature/update, generate 4 search queries
that would help find related content in a documentation corpus.

Cover these angles:
1. What the feature is and does (specific to this feature)
2. What existing features it relates to or extends
3. What UI areas or workflows it affects
4. All {product_area} release notes and updates (broad product area sweep — use "{product_area} release notes updates features" as the basis)

Return ONLY a JSON array of 4 strings, nothing else.

PM Document:
{pm_doc[:3000]}"""

        response = self.run(prompt)
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except json.JSONDecodeError:
            return [pm_doc[:200], f"{product_area} release notes updates"]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer for BM25."""
        return re.findall(r'\w+', text.lower())

    @staticmethod
    def _bm25_score(query_tokens: list[str], doc_tokens: list[str],
                    avg_dl: float, n_docs: int, df: dict[str, int],
                    k1: float = 1.5, b: float = 0.75) -> float:
        """BM25 score for a single document against a query."""
        doc_len = len(doc_tokens)
        tf = Counter(doc_tokens)
        score = 0.0
        for term in query_tokens:
            if term not in tf:
                continue
            term_tf = tf[term]
            term_df = df.get(term, 0)
            idf = math.log((n_docs - term_df + 0.5) / (term_df + 0.5) + 1)
            numerator = term_tf * (k1 + 1)
            denominator = term_tf + k1 * (1 - b + b * doc_len / max(avg_dl, 1))
            score += idf * numerator / denominator
        return score

    def _hybrid_search(self, queries: list[str], top_k_per_query: int = 15) -> list[dict]:
        """Hybrid search: vector search + BM25 reranking with reciprocal rank fusion.

        1. Run vector search via Pinecone for each query
        2. Score the returned chunks with BM25 against all queries
        3. Combine vector rank and BM25 rank via reciprocal rank fusion
        """
        model = self._get_embed_model()
        all_results = {}

        # Step 1: Vector search (same as before)
        for query in queries:
            embedding = model.encode(query, normalize_embeddings=True).tolist()
            results = search(embedding, top_k=top_k_per_query)
            for r in results:
                chunk_id = r["id"]
                if chunk_id not in all_results or r["score"] > all_results[chunk_id]["score"]:
                    all_results[chunk_id] = r

        if not all_results:
            return []

        # Step 2: BM25 scoring on retrieved chunks
        chunks = list(all_results.values())
        doc_texts = [r["metadata"].get("content", "") for r in chunks]
        doc_token_lists = [self._tokenize(t) for t in doc_texts]

        # Compute document frequency for BM25
        n_docs = len(chunks)
        avg_dl = sum(len(d) for d in doc_token_lists) / max(n_docs, 1)
        df: dict[str, int] = {}
        for tokens in doc_token_lists:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        # Combined query tokens
        all_query_tokens = []
        for q in queries:
            all_query_tokens.extend(self._tokenize(q))

        bm25_scores = []
        for doc_tokens in doc_token_lists:
            score = self._bm25_score(all_query_tokens, doc_tokens, avg_dl, n_docs, df)
            bm25_scores.append(score)

        # Step 3: Reciprocal Rank Fusion (RRF)
        # Sort by vector score for vector ranking
        vector_ranking = sorted(range(len(chunks)), key=lambda i: chunks[i]["score"], reverse=True)
        # Sort by BM25 score for keyword ranking
        bm25_ranking = sorted(range(len(chunks)), key=lambda i: bm25_scores[i], reverse=True)

        k = 60  # RRF constant
        rrf_scores = [0.0] * len(chunks)
        for rank, idx in enumerate(vector_ranking):
            rrf_scores[idx] += 1.0 / (k + rank + 1)
        for rank, idx in enumerate(bm25_ranking):
            rrf_scores[idx] += 1.0 / (k + rank + 1)

        # Sort by combined RRF score
        final_ranking = sorted(range(len(chunks)), key=lambda i: rrf_scores[i], reverse=True)

        return [chunks[i] for i in final_ranking[:30]]

    def _product_area_sweep(self, product_area: str, top_k: int = 25) -> list[dict]:
        """Broad sweep: find all corpus pages related to this product area by URL/label match."""
        model = self._get_embed_model()
        # Cast a wide semantic net with multiple phrasings of the product area
        sweep_queries = [
            f"{product_area} features updates release notes",
            f"{product_area} documentation how to use",
        ]
        all_results = {}
        for query in sweep_queries:
            embedding = model.encode(query, normalize_embeddings=True).tolist()
            results = search(embedding, top_k=top_k)
            for r in results:
                chunk_id = r["id"]
                # Keep only results where source_url or ia_label contains the product area keyword
                url = r["metadata"].get("source_url", "").lower()
                label = r["metadata"].get("ia_label", "").lower()
                keyword = product_area.lower().split()[0]  # e.g. "verkos", "fleet", "mission"
                if keyword in url or keyword in label:
                    if chunk_id not in all_results or r["score"] > all_results[chunk_id]["score"]:
                        all_results[chunk_id] = r

        return sorted(all_results.values(), key=lambda x: x["score"], reverse=True)

    def research(self, pm_doc: str, ia_yaml_summary: str) -> dict:
        """Main entry point. Takes PM doc text, returns structured research output."""

        # Step 1: Extract product area
        product_area = self._extract_product_area(pm_doc)

        # Step 2: Generate sub-queries (includes a broad product area sweep query)
        sub_queries = self._generate_sub_queries(pm_doc, product_area)

        # Step 3: Hybrid search (vector + BM25) across all sub-queries
        corpus_results = self._hybrid_search(sub_queries)

        # Step 4: Dedicated product area sweep — catches pages that scored low on semantics
        sweep_results = self._product_area_sweep(product_area)

        # Step 5: Format both result sets for the LLM
        def format_results(results: list[dict], limit: int) -> list[dict]:
            formatted = []
            for r in results[:limit]:
                meta = r["metadata"]
                formatted.append({
                    "heading": meta.get("heading", ""),
                    "content_preview": meta.get("content", "")[:500],
                    "ia_node": meta.get("ia_node", ""),
                    "ia_label": meta.get("ia_label", ""),
                    "source_url": meta.get("source_url", ""),
                    "source_type": meta.get("source_type", ""),
                    "score": round(r["score"], 4),
                })
            return formatted

        corpus_context = format_results(corpus_results, 20)
        sweep_context = format_results(sweep_results, 15)

        # Step 5.5: Load placement corrections from memory
        placement_memory = ""
        placement_path = PROJECT_ROOT / "memory" / "placement_corrections.md"
        if placement_path.exists():
            text = placement_path.read_text().strip()
            if "No corrections yet" not in text:
                placement_memory = f"\n--- PLACEMENT CORRECTIONS (learned from reviewer feedback) ---\n{text}\n--- END CORRECTIONS ---\n"

        # Step 6: LLM analysis
        analysis_prompt = f"""Here is the PM document for a new feature/update:

--- PM DOCUMENT ---
{pm_doc}
--- END PM DOCUMENT ---

Here is the current IA (Information Architecture) structure:
--- IA STRUCTURE ---
{ia_yaml_summary}
--- END IA STRUCTURE ---

Here are the most relevant chunks from semantic search:
--- SEMANTIC SEARCH RESULTS ---
{json.dumps(corpus_context, indent=2)}
--- END SEMANTIC RESULTS ---

Here are ALL pages found in the "{product_area}" product area (URL/label match):
--- PRODUCT AREA SWEEP ('{product_area}') ---
{json.dumps(sweep_context, indent=2)}
--- END PRODUCT AREA SWEEP ---

Sub-queries used: {json.dumps(sub_queries)}
Product area identified: {product_area}
{placement_memory}
IMPORTANT: The product area sweep above shows every existing page related to {product_area}.
Check ALL of them for impacted_pages — not just the ones that scored highest semantically.
Any page that covers this feature area and would need a cross-reference or content update must be included.

Now analyze everything and produce the structured JSON output as specified in your instructions."""

        response = self.run(analysis_prompt)

        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except json.JSONDecodeError:
            return {
                "error": "Failed to parse research output",
                "raw_response": response,
                "sub_queries_used": sub_queries,
            }
