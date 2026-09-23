"""LLM backends.

- AnthropicLLM: live calls via the Messages API when ANTHROPIC_API_KEY is set.
- MockLLM: deterministic extractive answerer for offline dev, CI, and evals.

Both return raw text that the pipeline parses/validates against RAGAnswer.
The mock is NOT a stub that always succeeds: it answers extractively from the
provided context, so it exhibits real generation failures (picks a plausible
but wrong sentence) and real abstentions (low lexical support), which is what
makes the offline failure analysis meaningful.
"""
from __future__ import annotations

import json
import os
import re

from .retriever import tokenize

# USD per million tokens (input, output) - update as pricing changes
PRICE_TABLE = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "mock-extractive-v1": (0.0, 0.0),
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    p_in, p_out = PRICE_TABLE.get(model, (3.00, 15.00))
    return round(in_tok / 1e6 * p_in + out_tok / 1e6 * p_out, 6)


SYSTEM_PROMPT = (
    "You are a technical assistant answering ONLY from the provided context "
    "chunks. Respond with a single JSON object: "
    '{"answer": str, "citations": [chunk_id, ...], "confidence": 0..1, '
    '"abstained": bool}. Cite only chunk_ids whose text directly supports the '
    "answer. If the context does not contain the answer, set abstained=true, "
    'confidence<=0.2 and answer="The provided documents do not answer this." '
    "Return raw JSON only - no markdown fences, no prose."
)


class AnthropicLLM:
    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.model = model
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def complete(self, prompt: str) -> str:
        import urllib.request

        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": 700,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", []))


class MockLLM:
    """Deterministic extractive answerer.

    Scores every sentence in every context chunk by content-word overlap with
    the question (weighted by inverse frequency across the context), returns
    the best sentence as the answer, cites the chunk it came from, and
    abstains when lexical support is below threshold.
    """

    model = "mock-extractive-v1"
    ABSTAIN_THRESHOLD = 0.18

    def complete(self, prompt: str) -> str:
        question, chunks = self._parse_prompt(prompt)
        q_tokens = set(tokenize(question))

        # coverage guard: if most question terms never occur anywhere in the
        # retrieved context, the evidence cannot answer the question -> abstain.
        # (Added after eval q24 showed a false answer on an unanswerable query.)
        context_vocab = set()
        for _, text in chunks:
            context_vocab.update(tokenize(text))
        coverage = (
            len(q_tokens & context_vocab) / max(len(q_tokens), 1) if q_tokens else 0.0
        )
        if coverage < 0.55:
            return json.dumps(
                {
                    "answer": "The provided documents do not answer this.",
                    "citations": [],
                    "confidence": round(coverage * 0.3, 2),
                    "abstained": True,
                }
            )

        best = None  # (score, sentence, chunk_id)
        for chunk_id, text in chunks:
            # split on sentence enders only - NOT colons; splitting on ':'
            # truncated "bulk/absorb at 55.2-56.0 V" style answers (eval q10)
            for sent in re.split(r"(?<=[.;])\s+(?=[A-Z(])", text):
                s_tokens = set(tokenize(sent))
                if not s_tokens:
                    continue
                overlap = len(q_tokens & s_tokens)
                score = overlap / max(len(q_tokens), 1)
                # prefer denser sentences slightly (numbers over filler)
                if any(ch.isdigit() for ch in sent):
                    score += 0.02
                if best is None or score > best[0]:
                    best = (score, sent.strip(), chunk_id)
        if best is None or best[0] < self.ABSTAIN_THRESHOLD:
            return json.dumps(
                {
                    "answer": "The provided documents do not answer this.",
                    "citations": [],
                    "confidence": round(best[0], 2) if best else 0.0,
                    "abstained": True,
                }
            )
        score, sentence, chunk_id = best
        return json.dumps(
            {
                "answer": sentence,
                "citations": [chunk_id],
                "confidence": round(min(0.95, 0.3 + score), 2),
                "abstained": False,
            }
        )

    @staticmethod
    def _parse_prompt(prompt: str) -> tuple[str, list[tuple[str, str]]]:
        chunks = re.findall(
            r"\[chunk_id=([^\]]+)\]\n(.*?)(?=\n\[chunk_id=|\nQUESTION:)",
            prompt,
            flags=re.DOTALL,
        )
        qm = re.search(r"QUESTION:\s*(.+)$", prompt, flags=re.DOTALL)
        question = qm.group(1).strip() if qm else prompt
        return question, [(cid.strip(), text.strip()) for cid, text in chunks]


def get_llm():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(os.environ.get("RAG_MODEL", "claude-haiku-4-5-20251001"))
    return MockLLM()
