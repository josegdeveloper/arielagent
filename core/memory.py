"""
core/memory.py — Conversation and long-term memory manager with semantic search.

Handles three types of memory:
  - Short-term (session): The current conversation history sent to the API.
    Reset at the start of each new task.
  - Long-term (persistent): Summaries saved to longtermmemory.json that
    survive across sessions, giving ARIEL context about the user.
  - Embeddings (semantic index): Vector representations of each long-term
    memory, stored in embeddings.json. Used to retrieve only the most
    relevant memories for a given task via cosine similarity.

The embedding system uses the 'all-MiniLM-L6-v2' model from the
sentence-transformers library. If the library is not installed, the
system gracefully falls back to returning the most recent memories.
"""

import uuid
import math
from typing import Any, List, Dict, Optional
from datetime import datetime
from core.logger import LoggerManager
from core.utils import BASE_DIR, load_json, save_json


# ═══════════════════════════════════════════════════════════════════
#  EMBEDDING MANAGER
#  Handles vector generation and similarity search for semantic memory.
# ═══════════════════════════════════════════════════════════════════

class EmbeddingManager:
    """Generates and compares text embeddings for semantic memory search.

    Uses the 'all-MiniLM-L6-v2' model (384 dimensions, ~80MB).
    The model is loaded lazily — only when the first embedding is
    actually needed — so startup isn't slowed down if no memory
    operations happen.

    If sentence-transformers is not installed, all methods degrade
    gracefully: is_available() returns False, and the MemoryManager
    falls back to a simple "most recent N" strategy.
    """

    # Multilingual model that supports 50+ languages including Spanish.
    # Same 384 dimensions as the English-only model, similar size (~120MB).
    # This ensures semantic search works correctly regardless of whether
    # the user writes in Spanish, English, or mixes both.
    MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self, logger: LoggerManager):
        self.logger = logger
        self._model = None       # Lazy-loaded SentenceTransformer instance
        self._available = None   # None = not checked yet, True/False after check

    def is_available(self) -> bool:
        """Check if sentence-transformers is installed and usable.

        The result is cached after the first call so subsequent checks
        don't import the library repeatedly.
        """
        if self._available is None:
            try:
                from sentence_transformers import SentenceTransformer  # noqa: F401
                self._available = True
                self.logger.info("Embedding engine available (sentence-transformers found).")
            except ImportError:
                self._available = False
                self.logger.warning(
                    "sentence-transformers not installed. "
                    "Memory search will fall back to most-recent strategy. "
                    "Install with: pip install sentence-transformers"
                )
        return self._available

    def _load_model(self):
        """Load the embedding model into memory (first time only).

        This can take a few seconds on the first call as it downloads
        the model if not cached. Subsequent calls are instant.

        Suppresses all noisy output from the HuggingFace ecosystem:
          - "unauthenticated requests" warning from HF Hub
          - "BertModel LOAD REPORT" table from model loading
          - "Loading weights" tqdm progress bar
          - "UNEXPECTED key: embeddings.position_ids" warning
        These are all harmless but alarming to non-technical users.
        """
        if self._model is None:
            import warnings
            import logging
            import os
            import io
            import sys

            # Suppress the "unauthenticated requests" warning from HF Hub
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            os.environ["TOKENIZERS_PARALLELISM"] = "false"

            # Temporarily silence noisy loggers from the HuggingFace ecosystem
            noisy_loggers = [
                "sentence_transformers", "transformers", "huggingface_hub",
                "torch", "filelock", "fsspec", "urllib3"
            ]
            original_levels = {}
            for name in noisy_loggers:
                lg = logging.getLogger(name)
                original_levels[name] = lg.level
                lg.setLevel(logging.ERROR)

            from sentence_transformers import SentenceTransformer

            self.logger.info(f"Loading embedding model '{self.MODEL_NAME}'...")

            # Suppress ALL output during model instantiation:
            # - Python warnings (position_ids mismatch, etc.)
            # - Direct stderr prints (tqdm progress bar, LOAD REPORT table)
            # - Direct stdout prints (HF Hub auth warning)
            original_stderr = sys.stderr
            original_stdout = sys.stdout
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                sys.stderr = io.StringIO()  # Redirect stderr to a black hole
                sys.stdout = io.StringIO()  # Redirect stdout too
                try:
                    self._model = SentenceTransformer(self.MODEL_NAME)
                finally:
                    sys.stderr = original_stderr  # Always restore
                    sys.stdout = original_stdout

            # Restore original logging levels so other code isn't affected
            for name, level in original_levels.items():
                logging.getLogger(name).setLevel(level)

            self.logger.info("Embedding model loaded successfully.")

    def generate(self, text: str) -> List[float]:
        """Convert a text string into a 384-dimensional vector.

        Args:
            text: Any text (memory content, user query, etc.).

        Returns:
            A list of 384 floats representing the text's semantic meaning.
        """
        self._load_model()
        vector = self._model.encode(text)
        return vector.tolist()

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Calculate the cosine similarity between two vectors.

        Uses pure Python math (no numpy dependency) so it works even
        in minimal environments. The performance difference is negligible
        for the small number of vectors we deal with (hundreds, not millions).

        Returns:
            A float between -1.0 (opposite) and 1.0 (identical).
        """
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        # Avoid division by zero for empty or zero vectors
        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)


# ═══════════════════════════════════════════════════════════════════
#  MEMORY MANAGER
#  Orchestrates short-term conversation and long-term persistent memory.
# ═══════════════════════════════════════════════════════════════════

class MemoryManager:
    """Manages the current conversation history and persistent long-term memory.

    On initialization, it checks for any long-term memories that are
    missing an ID or an embedding, and automatically migrates them.
    This ensures the system stays consistent even after manual edits
    to the JSON files.
    """

    # ── File paths ──────────────────────────────────────────────────
    MEMORY_DIR = BASE_DIR / "memory"
    LT_MEMORY_PATH = MEMORY_DIR / "longtermmemory.json"
    EMBEDDINGS_PATH = MEMORY_DIR / "embeddings.json"

    def __init__(self, logger: LoggerManager):
        """Initialize memory manager, embedding engine, and run migration."""
        self.logger = logger
        self.messages: List[Dict[str, Any]] = []  # Short-term conversation history

        # Ensure the memory directory exists
        self.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        # Initialize the embedding engine (lazy — model loads on first use)
        self.embeddings = EmbeddingManager(logger)

        # Migrate any existing memories that lack IDs or embeddings
        self._migrate_memories()

    # ── Short-term memory (conversation) ────────────────────────────

    def reset(self):
        """Clear the short-term conversation history.

        Called at the start of each new task so previous context
        doesn't bleed into unrelated tasks.
        """
        self.messages = []
        self.logger.info("Short-term memory reset.")

    def add_user_message(self, content: Any):
        """Append a user message (text or tool results) to the conversation."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: Any):
        """Append an assistant response to the conversation."""
        self.messages.append({"role": "assistant", "content": content})

    def get_api_messages(self) -> List[Dict[str, Any]]:
        """Return the full conversation history in the format the API expects."""
        return self.messages

    # ── Long-term memory (persistent + semantic search) ─────────────

    def update_long_term_memory(self, summary: str):
        """Save a new memory with its embedding vector.

        Steps:
          1. Generate a unique ID for the memory.
          2. Append it to longtermmemory.json.
          3. Generate an embedding vector and store it in embeddings.json.
          4. If embeddings are unavailable, the memory is still saved
             (it just won't be searchable semantically).
        """
        # Generate a unique 8-character ID
        memory_id = str(uuid.uuid4())[:8]

        # ── Save the memory text ────────────────────────────────────
        lt_data = load_json(self.LT_MEMORY_PATH)
        if "memories" not in lt_data:
            lt_data = {"memories": []}

        lt_data["memories"].append({
            "id": memory_id,
            "timestamp": datetime.now().isoformat(),
            "content": summary
        })
        save_json(self.LT_MEMORY_PATH, lt_data)

        # ── Generate and save the embedding ─────────────────────────
        if self.embeddings.is_available():
            try:
                vector = self.embeddings.generate(summary)

                emb_data = load_json(self.EMBEDDINGS_PATH)
                if "embeddings" not in emb_data:
                    emb_data = {"embeddings": {}}

                emb_data["embeddings"][memory_id] = vector
                emb_data["model"] = EmbeddingManager.MODEL_NAME  # Track which model generated these
                save_json(self.EMBEDDINGS_PATH, emb_data)

                self.logger.info(f"Memory '{memory_id}' saved with embedding ({len(vector)} dims).")
            except Exception as e:
                self.logger.error(f"Failed to generate embedding for memory '{memory_id}': {e}")
        else:
            self.logger.info(f"Memory '{memory_id}' saved (no embedding — fallback mode).")

    def search_relevant_memories(self, query: str, top_k: int = 5) -> List[str]:
        """Find the most relevant long-term memories for a given query.

        If embeddings are available:
          1. Convert the query to a vector.
          2. Compute cosine similarity against all stored memory vectors.
          3. Return the top_k most similar memory texts.

        If embeddings are NOT available (fallback):
          Return the most recent top_k memories (same as the old behavior).

        Args:
            query: The user's task or search query.
            top_k: Maximum number of memories to return.

        Returns:
            A list of memory content strings, sorted by relevance.
        """
        lt_data = load_json(self.LT_MEMORY_PATH)
        memories = lt_data.get("memories", [])

        if not memories:
            return ["No previous memories."]

        # ── Semantic search path ────────────────────────────────────
        if self.embeddings.is_available():
            try:
                emb_data = load_json(self.EMBEDDINGS_PATH)
                stored_embeddings = emb_data.get("embeddings", {})

                # Only search memories that have a corresponding embedding
                searchable = [m for m in memories if m.get("id") in stored_embeddings]

                if not searchable:
                    # No embeddings available — fall through to fallback
                    self.logger.warning("No embeddings found. Using fallback.")
                else:
                    # Generate a vector for the user's query
                    query_vector = self.embeddings.generate(query)

                    # Score each memory by cosine similarity
                    scored = []
                    for mem in searchable:
                        mem_vector = stored_embeddings[mem["id"]]
                        score = EmbeddingManager.cosine_similarity(query_vector, mem_vector)
                        scored.append((score, mem))

                    # Sort by similarity (highest first) and take top_k
                    scored.sort(key=lambda x: x[0], reverse=True)
                    top_memories = scored[:top_k]

                    self.logger.info(
                        f"Semantic search: {len(searchable)} memories indexed, "
                        f"returning top {len(top_memories)} "
                        f"(best score: {top_memories[0][0]:.3f})."
                    )

                    return [m["content"] for _, m in top_memories]

            except Exception as e:
                self.logger.error(f"Semantic search failed: {e}. Using fallback.")

        # ── Fallback: return most recent memories ───────────────────
        self.logger.info(f"Using fallback: returning last {top_k} memories.")
        recent = memories[-top_k:]
        return [m["content"] for m in recent]

    def keyword_search(self, keywords: List[str], max_results: int = 5) -> List[Dict]:
        """Search long-term memories by keywords (used by the memory_search tool).

        Combines semantic similarity (if available) with keyword matching
        for the best results. Falls back to pure keyword matching if
        embeddings are unavailable.

        Args:
            keywords: List of search terms.
            max_results: Maximum number of results to return.

        Returns:
            A list of dicts with 'content', 'timestamp', and 'score' keys.
        """
        lt_data = load_json(self.LT_MEMORY_PATH)
        memories = lt_data.get("memories", [])

        if not memories:
            return []

        query_text = " ".join(keywords)
        results = []

        # ── Score with embeddings if available ──────────────────────
        if self.embeddings.is_available():
            try:
                emb_data = load_json(self.EMBEDDINGS_PATH)
                stored = emb_data.get("embeddings", {})
                query_vec = self.embeddings.generate(query_text)

                for mem in memories:
                    mem_id = mem.get("id", "")
                    # Combine semantic score with keyword bonus
                    semantic_score = 0.0
                    if mem_id in stored:
                        semantic_score = EmbeddingManager.cosine_similarity(query_vec, stored[mem_id])

                    # Keyword bonus: +0.1 for each keyword found in the content
                    content_lower = mem.get("content", "").lower()
                    keyword_bonus = sum(0.1 for kw in keywords if kw.lower() in content_lower)

                    total_score = semantic_score + keyword_bonus
                    results.append({
                        "content": mem["content"],
                        "timestamp": mem.get("timestamp", ""),
                        "score": round(total_score, 3)
                    })

                results.sort(key=lambda x: x["score"], reverse=True)
                return results[:max_results]

            except Exception as e:
                self.logger.error(f"Keyword+semantic search failed: {e}")

        # ── Fallback: pure keyword matching ─────────────────────────
        for mem in memories:
            content_lower = mem.get("content", "").lower()
            matches = sum(1 for kw in keywords if kw.lower() in content_lower)
            if matches > 0:
                results.append({
                    "content": mem["content"],
                    "timestamp": mem.get("timestamp", ""),
                    "score": matches
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:max_results]

    # ── Migration ───────────────────────────────────────────────────

    def _migrate_memories(self):
        """Ensure all long-term memories have IDs and compatible embeddings.

        This runs on every startup and handles:
          - Memories created before the embedding system was added (no 'id' field).
          - Memories whose embedding was lost or never generated.
          - Orphan embeddings whose memory was deleted.
          - Model changes: if the embedding model has been updated (e.g.
            from English-only to multilingual), ALL embeddings are regenerated
            automatically so search results remain accurate.

        The migration is idempotent — running it multiple times is safe.
        """
        lt_data = load_json(self.LT_MEMORY_PATH)
        memories = lt_data.get("memories", [])

        if not memories:
            return

        emb_data = load_json(self.EMBEDDINGS_PATH)
        if "embeddings" not in emb_data:
            emb_data = {"embeddings": {}}

        needs_save_lt = False    # Flag: do we need to rewrite longtermmemory.json?
        needs_save_emb = False   # Flag: do we need to rewrite embeddings.json?

        # ── Step 0: Detect model change ─────────────────────────────
        # If the model name stored in embeddings.json doesn't match the
        # current model, all existing embeddings are invalid and must be
        # regenerated from scratch.
        stored_model = emb_data.get("model", "")
        current_model = EmbeddingManager.MODEL_NAME

        if stored_model != current_model and emb_data["embeddings"]:
            self.logger.info(
                f"Migration: model changed from '{stored_model}' to '{current_model}'. "
                f"Regenerating all {len(emb_data['embeddings'])} embeddings..."
            )
            emb_data["embeddings"] = {}  # Wipe all old embeddings
            emb_data["model"] = current_model
            needs_save_emb = True

        # ── Step 1: Assign IDs to any memory that lacks one ─────────
        for mem in memories:
            if "id" not in mem or not mem["id"]:
                mem["id"] = str(uuid.uuid4())[:8]
                needs_save_lt = True
                self.logger.info(f"Migration: assigned ID '{mem['id']}' to memory.")

        # ── Step 2: Generate missing embeddings ─────────────────────
        if self.embeddings.is_available():
            for mem in memories:
                mem_id = mem.get("id", "")
                if mem_id and mem_id not in emb_data["embeddings"]:
                    try:
                        vector = self.embeddings.generate(mem["content"])
                        emb_data["embeddings"][mem_id] = vector
                        needs_save_emb = True
                        self.logger.info(f"Migration: generated embedding for memory '{mem_id}'.")
                    except Exception as e:
                        self.logger.error(f"Migration: failed to embed memory '{mem_id}': {e}")

            # ── Step 3: Clean up orphan embeddings ──────────────────
            valid_ids = {m.get("id") for m in memories if m.get("id")}
            orphan_ids = [eid for eid in emb_data["embeddings"] if eid not in valid_ids]
            for oid in orphan_ids:
                del emb_data["embeddings"][oid]
                needs_save_emb = True
                self.logger.info(f"Migration: removed orphan embedding '{oid}'.")

            # Always store the current model name
            emb_data["model"] = current_model

        # ── Persist changes ─────────────────────────────────────────
        if needs_save_lt:
            save_json(self.LT_MEMORY_PATH, lt_data)
            self.logger.info("Migration: longtermmemory.json updated.")

        if needs_save_emb:
            save_json(self.EMBEDDINGS_PATH, emb_data)
            self.logger.info("Migration: embeddings.json updated.")