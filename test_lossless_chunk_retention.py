import importlib.util
import os
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
DEV_V2_PATH = ROOT / "dev_v2.py"

os.environ.setdefault("OPENAI_API_KEY", "test")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_import_stubs() -> None:
    app_pkg = types.ModuleType("app")
    vector_logic_pkg = types.ModuleType("app.vector_logic")
    vector_store_mod = types.ModuleType("app.vector_logic.vector_store")
    vector_store_mod._embed_query = lambda *args, **kwargs: []
    vector_store_mod.EmbeddingDimensionMismatchError = Exception
    vector_store_mod.query_collection = lambda *args, **kwargs: {}
    vector_store_mod.query_collection_with_filter = lambda *args, **kwargs: {}
    vector_store_mod.query_master_collection = lambda *args, **kwargs: {}
    vector_store_mod.query_bm25 = lambda *args, **kwargs: []
    vector_store_mod.reciprocal_rank_fusion = lambda dense, sparse: list(dense) + list(sparse)
    vector_store_mod.rerank_chunks = lambda *args, **kwargs: []

    intent_router_mod = types.ModuleType("app.vector_logic.intent_router")
    intent_router_mod.QuestionIntent = object
    intent_router_mod.classify_intent = lambda *args, **kwargs: {}
    intent_router_mod.classify_intent_light = lambda *args, **kwargs: {"route": "RAG"}
    intent_router_mod.handle_classification = lambda *args, **kwargs: ""
    intent_router_mod.handle_conversational = lambda *args, **kwargs: ""
    intent_router_mod.handle_count = lambda *args, **kwargs: ""
    intent_router_mod.handle_domain_query = lambda *args, **kwargs: ""
    intent_router_mod.handle_existence = lambda *args, **kwargs: ""
    intent_router_mod.handle_listing = lambda *args, **kwargs: ""

    sqlite_pkg = types.ModuleType("app.sqlite")
    sqlite_models_mod = types.ModuleType("app.sqlite.models")
    sqlite_models_mod.Document = type("Document", (), {})
    sqlite_models_mod.Category = type("Category", (), {})
    sqlite_db_mod = types.ModuleType("app.sqlite.database")
    sqlite_db_mod.SessionLocal = lambda: None

    core_pkg = types.ModuleType("app.core")
    core_config_mod = types.ModuleType("app.core.config")
    core_config_mod.settings = types.SimpleNamespace(openai_api_key="test")

    sys.modules.setdefault("app", app_pkg)
    sys.modules["app.vector_logic"] = vector_logic_pkg
    sys.modules["app.vector_logic.vector_store"] = vector_store_mod
    sys.modules["app.vector_logic.intent_router"] = intent_router_mod
    sys.modules["app.sqlite"] = sqlite_pkg
    sys.modules["app.sqlite.models"] = sqlite_models_mod
    sys.modules["app.sqlite.database"] = sqlite_db_mod
    sys.modules["app.core"] = core_pkg
    sys.modules["app.core.config"] = core_config_mod


_install_import_stubs()

spec = importlib.util.spec_from_file_location("dev_v2_under_test", DEV_V2_PATH)
dev_v2 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(dev_v2)


class LosslessChunkRetentionTests(unittest.TestCase):
    def test_dedupes_dense_and_sparse_hits_and_preserves_reasons(self) -> None:
        candidates = [
            {
                "chunk_id": "c1",
                "document_id": 1,
                "collection_name": "cross_team_decks",
                "document_title": "Doc",
                "chunk_text": "Jagged Frontier ROI improved delivery outcomes.",
                "metadata": {"chunk_index": 4},
                "dense_rank": 1,
                "sparse_rank": None,
                "rrf_score": 0.2,
                "anchor_score": 1.0,
                "entity_score": 1.0,
                "query_type_score": 0.0,
                "final_score": 0.0,
                "selection_reason": ["dense_hit", "entity_match"],
                "retrieval_stage": "pass1",
                "exact_phrase_hits": ["jagged frontier"],
            },
            {
                "chunk_id": "c1",
                "document_id": 1,
                "collection_name": "cross_team_decks",
                "document_title": "Doc",
                "chunk_text": "Jagged Frontier ROI improved delivery outcomes.",
                "metadata": {"chunk_index": 4},
                "dense_rank": None,
                "sparse_rank": 1,
                "rrf_score": 0.25,
                "anchor_score": 1.0,
                "entity_score": 1.0,
                "query_type_score": 0.0,
                "final_score": 0.0,
                "selection_reason": ["bm25_hit"],
                "retrieval_stage": "pass1",
                "exact_phrase_hits": ["jagged frontier"],
            },
        ]

        selected, debug = dev_v2._select_lossless_chunks_for_doc(
            candidates=candidates,
            user_query="What is the ROI for Jagged Frontier?",
            anchors=["Jagged Frontier", "ROI"],
            entities=["Jagged Frontier"],
            answer_mode="partial_ok",
            per_doc_limit=1,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["chunk_id"], "c1")
        self.assertIn("dense_hit", selected[0]["selection_reason"])
        self.assertIn("bm25_hit", selected[0]["selection_reason"])
        self.assertGreaterEqual(debug["duplicates_merged"], 1)

    def test_must_keep_entity_chunk_survives_low_cap(self) -> None:
        strong_entity = {
            "chunk_id": "entity-1",
            "document_id": 7,
            "collection_name": "cross_team_decks",
            "document_title": "Doc",
            "chunk_text": "Jagged Frontier had measurable ROI and profitability gains.",
            "metadata": {"chunk_index": 2},
            "dense_rank": 5,
            "sparse_rank": 5,
            "rrf_score": 0.05,
            "anchor_score": 1.0,
            "entity_score": 1.0,
            "query_type_score": 0.0,
            "final_score": 0.0,
            "selection_reason": ["entity_match"],
            "retrieval_stage": "pass1",
            "exact_phrase_hits": [],
        }
        generic_top = {
            "chunk_id": "generic-1",
            "document_id": 7,
            "collection_name": "cross_team_decks",
            "document_title": "Doc",
            "chunk_text": "General program overview and delivery summary.",
            "metadata": {"chunk_index": 1},
            "dense_rank": 1,
            "sparse_rank": 1,
            "rrf_score": 0.4,
            "anchor_score": 0.0,
            "entity_score": 0.0,
            "query_type_score": 0.0,
            "final_score": 0.0,
            "selection_reason": ["dense_hit"],
            "retrieval_stage": "pass1",
            "exact_phrase_hits": [],
        }

        selected, debug = dev_v2._select_lossless_chunks_for_doc(
            candidates=[generic_top, strong_entity],
            user_query="Tell me the ROI for Jagged Frontier",
            anchors=["ROI"],
            entities=["Jagged Frontier"],
            answer_mode="partial_ok",
            per_doc_limit=1,
        )

        selected_ids = {chunk["chunk_id"] for chunk in selected}
        self.assertIn("entity-1", selected_ids)
        self.assertGreaterEqual(debug["temporary_cap"], 1)


if __name__ == "__main__":
    unittest.main()
