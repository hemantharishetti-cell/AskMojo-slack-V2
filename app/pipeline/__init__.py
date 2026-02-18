"""
Pipeline modules for the 3-stage RAG architecture.

Stage 1: Query Understanding  (intent.py, query_rewrite.py, metadata_handler.py)
Stage 2: Retrieval            (retrieval.py, chunk_scorer.py)
Stage 3: Response Synthesis   (response_generator.py, model_selector.py)

Orchestrated by: orchestrator.py
"""
