"""Tests opt into these settings explicitly; production never imports this module."""

from llm.core.models import ProjectConfig
from llm.components.rag import RAGComponent
from llm.components.memory import MemoryComponent
from llm.engines.loop import LoopEngine


async def configure_engine(project, name, **settings):
    """테스트가 지정한 대상 설정만 일반 Project 저장 API로 갱신한다."""
    config = (await project.aget_data()).config
    engines = config.parameters.setdefault("engines", {})
    engines[name] = ProjectConfig.merge(engines.get(name, {}), LoopEngine.parameter_layout.pack(settings))
    await project.asave(config=config)


def rag_settings(changes=None):
    return ProjectConfig.merge(RAGComponent.parameter_layout.pack({
        "chunk_size": 2000, "embedding_concurrency": 2,
        "embedding_cache_max_bytes": 16 * 1024 * 1024,
        "extraction_batch_size": 32, "search_cache_chars": 1_000_000,
        "index_batch_size": 128,
        "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2},
        "search": {"method": "hybrid", "expand": "section", "limit": 5,
                   "rerank": False, "max_hops": 2, "relation_limit": 30,
                   "candidate_count": 20, "rrf_constant": 60},
        "extraction": {"failure_policy": "required"},
    }), changes or {})


def rag_project(config=None):
    result = ProjectConfig(config)
    result.parameters.setdefault("components", {})["rag"] = rag_settings(result.parameters.setdefault("components", {}).get("rag"))
    return result


def memory_processing(changes=None):
    return ProjectConfig.merge({
        "priority": 100, "recall": True, "summarize": False, "extract": False, "compress_tools": True,
        "keep_turns": 8, "summary_after_chars": 12000, "summary_chars": 3000,
        "context_chars": 6000, "recall_limit": 8, "tool_result_chars": 4000,
        "model_input_chars": 24000, "max_candidates": 5, "extract_scope": "session",
        "compact_active": True, "active_keep_iterations": 2, "max_summary_calls": 4,
        "recall_query_chars": 2000,
    }, changes or {})


def memory_settings(explicit):
    """processor 테스트의 명시적 인자들을 공개 설정 외형에 배치한다."""
    return MemoryComponent.parameter_layout.pack({"search_strategy": "keyword", **explicit})
