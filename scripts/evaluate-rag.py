#!/usr/bin/env python3
"""对 JSONL 标注集计算 RAG Recall@K、MRR、拒答准确率和延迟。"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from infrastructure.knowledge_base import KnowledgeBase


def evaluate(dataset_path: Path, docs_dir: Path, k: int) -> dict[str, object]:
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    knowledge_base = KnowledgeBase(docs_dir, top_k=k, min_score=1.0)
    knowledge_base.load()
    relevant_count = hits = 0
    reciprocal_ranks: list[float] = []
    negative_count = negative_correct = 0
    latencies: list[float] = []
    failures: list[dict[str, object]] = []

    for case in cases:
        started = perf_counter()
        results = knowledge_base.retrieve(case["query"], k=k)
        latencies.append((perf_counter() - started) * 1000)
        if not case.get("relevant", True):
            negative_count += 1
            negative_correct += int(not results)
            if results:
                failures.append({"query": case["query"], "reason": "false_positive"})
            continue

        relevant_count += 1
        rank = 0
        for index, result in enumerate(results, 1):
            source_ok = result.source == case.get("expected_source")
            heading_ok = case.get("heading_contains", "") in result.heading
            if source_ok and heading_ok:
                rank = index
                break
        if rank:
            hits += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)
            failures.append({"query": case["query"], "reason": "miss"})

    return {
        "cases": len(cases),
        f"recall@{k}": round(hits / relevant_count, 4) if relevant_count else None,
        "mrr": round(statistics.mean(reciprocal_ranks), 4)
        if reciprocal_ranks
        else None,
        "no_answer_accuracy": (
            round(negative_correct / negative_count, 4) if negative_count else None
        ),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_max_ms": round(max(latencies), 3),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=BACKEND_DIR / "rag_evalset.jsonl"
    )
    parser.add_argument("--docs", type=Path, default=BACKEND_DIR / "knowledge_base")
    parser.add_argument("-k", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.dataset, args.docs, args.k), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
