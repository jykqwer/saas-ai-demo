#!/usr/bin/env python3
"""对大规模合成文档执行可重复的 RAG 加载、延迟与 Top-1 召回基准。"""

from __future__ import annotations

import argparse
import json
import re
import resource
import statistics
import sys
from pathlib import Path
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from infrastructure.knowledge_base import KnowledgeBase

ANCHOR_RE = re.compile(r"检索锚点：`(cloudhub-[a-z]+-\d{6}-[a-f0-9]{12})`")


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(len(ordered) * ratio) - 1))
    return ordered[position]


def _sample_anchors(document: Path, count: int) -> list[str]:
    anchors = ANCHOR_RE.findall(document.read_text(encoding="utf-8"))
    if not anchors:
        raise ValueError(f"no retrieval anchors found in {document}")
    count = min(count, len(anchors))
    return [anchors[index * len(anchors) // count] for index in range(count)]


def benchmark(document: Path, samples: int) -> dict[str, object]:
    started = perf_counter()
    knowledge_base = KnowledgeBase(document.parent, top_k=3, min_score=1.0)
    chunk_count = knowledge_base.load()
    load_seconds = perf_counter() - started

    latencies: list[float] = []
    top1_hits = 0
    anchors = _sample_anchors(document, samples)
    for anchor in anchors:
        partial_anchor = anchor.rsplit("-", 1)[0]
        query = f"{partial_anchor} 的维护窗口和故障码"
        query_started = perf_counter()
        results = knowledge_base.retrieve(query)
        latencies.append((perf_counter() - query_started) * 1000)
        if results and anchor in results[0].content:
            top1_hits += 1

    return {
        "document": str(document.relative_to(REPO_ROOT)),
        "document_bytes": document.stat().st_size,
        "documents": knowledge_base.document_count,
        "chunks": chunk_count,
        "samples": len(anchors),
        "partial_anchor_top1_hits": top1_hits,
        "partial_anchor_top1_rate": round(top1_hits / len(anchors), 4),
        "load_seconds": round(load_seconds, 4),
        "retrieve_p50_ms": round(statistics.median(latencies), 3),
        "retrieve_p95_ms": round(_percentile(latencies, 0.95), 3),
        "retrieve_max_ms": round(max(latencies), 3),
        "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document",
        type=Path,
        default=BACKEND_DIR / "rag_testdata" / "cloudhub-product-manual-10mb.md",
    )
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    document = args.document.resolve()
    if not document.is_file():
        parser.error(f"document does not exist: {document}")
    print(json.dumps(benchmark(document, args.samples), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
