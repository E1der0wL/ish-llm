# Create a RAG document

Use when the user wants material registered for later retrieval.

1. Identify the requested source and purpose. Read an authorized file with `file_read`, or use
   the supplied text or verified response. Do not invent source content or include credentials.
2. Preserve headings, code, context and useful source references. Choose a new stable identifier
   and a clear title. Large material can be divided at meaningful section boundaries.
3. Call `rag_add_document` with identifier, title, content and optional source metadata. This
   public API builds the corpus index; writing JSON files in component storage does not.
4. Wait for the actual result. If embedding configuration is missing, explain which model
   needs configuration via `/rag config` → Settings. Do not guess a model or API key.
5. Check retrieval with `rag_search`, using an explicit query that should find the document.
   Use `rerank=false` when no reranker is configured. Distinguish successful storage from
   successful retrieval and report a failed verification honestly.

Registration does not authorize subsequent unrelated ingestion, network uploads, or model calls
outside the existing project policies. Preserve existing IDs rather than overwriting documents.
