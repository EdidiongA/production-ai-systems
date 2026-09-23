"""RAG pipeline: retrieve -> prompt -> generate -> validate -> log."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .chunker import load_corpus
from .llm import estimate_cost, estimate_tokens, get_llm
from .retriever import BM25Retriever
from .schemas import QueryResult, RAGAnswer, SchemaViolation

REPAIR_SUFFIX = (
    "\n\nYour previous reply was not valid JSON matching the required schema. "
    "Return ONLY the JSON object, nothing else."
)


class RAGPipeline:
    def __init__(self, docs_dir: str | Path, top_k: int = 4, log_path: str | Path | None = None):
        self.chunks = load_corpus(docs_dir)
        self.retriever = BM25Retriever(self.chunks)
        self.llm = get_llm()
        self.top_k = top_k
        self.log_path = Path(log_path) if log_path else None

    def build_prompt(self, query: str, scored) -> str:
        parts = ["CONTEXT CHUNKS:"]
        for sc in scored:
            parts.append(f"[chunk_id={sc.chunk.chunk_id}]\n{sc.chunk.text}")
        parts.append(f"QUESTION: {query}")
        return "\n".join(parts)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(raw)

    def ask(self, query: str) -> QueryResult:
        t0 = time.perf_counter()
        scored = self.retriever.search(query, top_k=self.top_k)
        prompt = self.build_prompt(query, scored)

        retries = 0
        raw = self.llm.complete(prompt)
        try:
            answer = RAGAnswer.model_validate(self._parse_json(raw))
        except Exception:  # noqa: BLE001 - ANY parse/validation failure must trigger the single repair retry
            retries = 1
            raw = self.llm.complete(prompt + REPAIR_SUFFIX)
            try:
                answer = RAGAnswer.model_validate(self._parse_json(raw))
            except Exception as exc:
                raise SchemaViolation(f"unparseable model output: {raw[:200]}") from exc

        # guardrail: citations must reference actually-retrieved chunks
        retrieved_ids = [sc.chunk.chunk_id for sc in scored]
        answer.citations = [c for c in answer.citations if c in retrieved_ids]
        if not answer.citations and not answer.abstained:
            # a non-abstained answer with no valid citation is downgraded
            answer.abstained = True
            answer.confidence = min(answer.confidence, 0.2)

        latency_ms = (time.perf_counter() - t0) * 1000
        in_tok = estimate_tokens(prompt) * (1 + retries)
        out_tok = estimate_tokens(raw)
        model = getattr(self.llm, "model", "unknown")
        qr = QueryResult(
            query=query,
            result=answer,
            retrieved_chunk_ids=retrieved_ids,
            retrieval_scores=[sc.score for sc in scored],
            latency_ms=round(latency_ms, 2),
            input_tokens=in_tok,
            output_tokens=out_tok,
            est_cost_usd=estimate_cost(model, in_tok, out_tok),
            model=model,
            schema_retries=retries,
        )
        self._log(qr)
        return qr

    def _log(self, qr: QueryResult) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(qr.model_dump_json() + "\n")
