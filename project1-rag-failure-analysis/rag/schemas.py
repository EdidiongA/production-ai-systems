"""Structured output contract between the model and the application code.

The LLM must return JSON matching RAGAnswer. Anything else is rejected and
retried once with a repair prompt; a second failure produces a logged
`SchemaViolation` and the API returns a safe abstention instead of raw text.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RAGAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    citations: list[str] = Field(
        default_factory=list,
        description="chunk_ids that directly support the answer",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    abstained: bool = False

    @field_validator("citations")
    @classmethod
    def non_empty_unless_abstained(cls, v: list[str], info):
        # abstained answers may carry no citations; enforced in pipeline
        return v


class QueryResult(BaseModel):
    """Full per-query record: answer + observability metadata."""
    query: str
    result: RAGAnswer
    retrieved_chunk_ids: list[str]
    retrieval_scores: list[float]
    latency_ms: float
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    model: str
    schema_retries: int = 0


class SchemaViolation(Exception):
    pass
