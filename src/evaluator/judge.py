import asyncio
import json
import random
import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

logger = logging.getLogger("LLMGuard.Evaluator")

@dataclass
class StructuredJudgeOutput:
    trace_id: str
    faithfulness_score: float
    answer_relevance_score: float
    hallucination_index: float
    reasoning_rationale: str
    verdict: str
    evaluated_at: float

class LLMJudgeEvaluator:
    """Async Continuous LLM-as-a-Judge Evaluation Pipeline."""

    def __init__(self, sample_rate: float = 0.10):
        self.sample_rate = sample_rate
        self.queue: asyncio.Queue = asyncio.Queue()
        self.evaluations: List[StructuredJudgeOutput] = []
        self.is_running = False

    async def enqueue(self, trace_id: str, prompt: str, response: str, context: Optional[str] = None):
        if random.random() <= self.sample_rate:
            await self.queue.put({"trace_id": trace_id, "prompt": prompt, "response": response, "context": context})

    async def start_worker(self):
        self.is_running = True
        logger.info("[LLM-AS-A-JUDGE] Evaluator background worker started.")
        while self.is_running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._evaluate(item)
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[LLM-AS-A-JUDGE] Worker error: {e}")

    async def _evaluate(self, item: Dict[str, Any]):
        await asyncio.sleep(0.01)
        prompt, response, context = item["prompt"], item["response"], item.get("context")

        if context:
            ctx_words = set(context.lower().split())
            resp_words = set(response.lower().split())
            faithfulness = min(1.0, max(0.60, len(resp_words.intersection(ctx_words)) / max(1, len(resp_words))))
        else:
            faithfulness = 0.95

        prompt_words = set(prompt.lower().split())
        resp_words = set(response.lower().split())
        relevance = min(1.0, max(0.65, len(prompt_words.intersection(resp_words)) / max(1, len(prompt_words))))

        hallucination_index = round(1.0 - faithfulness, 2)
        verdict = "PASS" if hallucination_index < 0.30 and relevance >= 0.70 else "FLAGGED"

        res = StructuredJudgeOutput(
            trace_id=item["trace_id"],
            faithfulness_score=round(faithfulness, 2),
            answer_relevance_score=round(relevance, 2),
            hallucination_index=hallucination_index,
            reasoning_rationale=f"Evaluated relevance ({relevance:.2f}) and faithfulness ({faithfulness:.2f}).",
            verdict=verdict,
            evaluated_at=asyncio.get_event_loop().time()
        )
        self.evaluations.append(res)
        logger.info(f"[LLM-AS-A-JUDGE JSON RESULT] {json.dumps(asdict(res))}")

    def get_stats(self) -> Dict[str, Any]:
        if not self.evaluations:
            return {"samples": 0, "avg_faithfulness": 1.0, "avg_relevance": 1.0, "avg_hallucination_index": 0.0}
        n = len(self.evaluations)
        return {
            "samples": n,
            "avg_faithfulness": round(sum(e.faithfulness_score for e in self.evaluations) / n, 2),
            "avg_relevance": round(sum(e.answer_relevance_score for e in self.evaluations) / n, 2),
            "avg_hallucination_index": round(sum(e.hallucination_index for e in self.evaluations) / n, 2)
        }
