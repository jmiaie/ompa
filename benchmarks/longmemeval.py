"""
OMPA LongMemEval Benchmark.

Measures Recall@5 on a subset of LongMemEval-style questions using OMPA's
verbatim storage + semantic search. For reference, MemPalace reports 96.6% R@5
on LongMemEval for the verbatim-storage approach; that is MemPalace's result,
not something this script reproduces. With no --data-path, this script scores
a 20-item synthetic sample, so its number is a smoke test, not a LongMemEval score.

This script:
1. Loads a set of (context, question, answer) triples
2. Stores each context chunk in OMPA via ao.write()
3. Queries ao.search() for each question
4. Checks if the correct answer appears in the top-5 results
5. Reports Recall@5, mean rank of correct answer, and latency

Usage:
    pip install ompa[all]          # Requires semantic search
    python benchmarks/longmemeval.py
    python benchmarks/longmemeval.py --k 5 --limit 50

The built-in dataset is a 20-item sample representative of LongMemEval's
question types. For the full evaluation, point --data-path at your own
LongMemEval JSONL file with {"context": str, "question": str, "answer": str}.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from ompa import Ompa

# ---------------------------------------------------------------------------
# Built-in sample dataset (representative LongMemEval question types)
# ---------------------------------------------------------------------------

SAMPLE_DATA = [
    {
        "context": "On March 14th, the team decided to migrate the authentication service from JWT to session tokens. The primary motivation was simplifying token rotation and reducing the risk of token theft in XSS scenarios.",
        "question": "What authentication mechanism did the team decide to migrate to?",
        "answer": "session tokens",
    },
    {
        "context": "The Q2 planning meeting concluded with the decision to postpone the mobile app launch by 6 weeks to allow more time for accessibility compliance testing.",
        "question": "Why was the mobile app launch postponed?",
        "answer": "accessibility compliance testing",
    },
    {
        "context": "Kai joined the Orion project as the new tech lead on January 8th. Her first task was reviewing the existing microservices architecture and identifying consolidation opportunities.",
        "question": "Who is the tech lead for the Orion project?",
        "answer": "Kai",
    },
    {
        "context": "The team adopted PostgreSQL as the primary database after a two-week evaluation that included MySQL, CockroachDB, and SQLite. The key factors were JSONB support, mature tooling, and the team's existing expertise.",
        "question": "What database was chosen as the primary database?",
        "answer": "PostgreSQL",
    },
    {
        "context": "On April 2nd, the production API experienced a 23-minute outage caused by a memory leak in the Redis connection pool. The fix was deployed at 14:47 UTC and all services recovered by 15:03 UTC.",
        "question": "What caused the production API outage?",
        "answer": "memory leak in the Redis connection pool",
    },
    {
        "context": "The retrospective for sprint 18 identified that code review turnaround time was the biggest bottleneck — average 3.2 days per PR. The team agreed to adopt a 24-hour SLA for first review.",
        "question": "What was the main bottleneck identified in the sprint 18 retrospective?",
        "answer": "code review turnaround time",
    },
    {
        "context": "The new rate limiting policy caps each API key at 1000 requests per minute. Keys that exceed this limit are throttled for 60 seconds before being allowed to retry.",
        "question": "What is the API rate limit per key?",
        "answer": "1000 requests per minute",
    },
    {
        "context": "The data pipeline team decided to use Apache Kafka for event streaming after evaluating RabbitMQ and AWS SQS. Kafka was selected for its high throughput, replay capability, and existing team familiarity.",
        "question": "What message broker was selected for event streaming?",
        "answer": "Apache Kafka",
    },
    {
        "context": "Bex completed the security audit on February 20th. The audit found two medium-severity issues in the password reset flow and one low-severity issue in session cookie flags. All three were patched within 48 hours.",
        "question": "Who completed the security audit?",
        "answer": "Bex",
    },
    {
        "context": "The team agreed to move daily standups from 9 AM to 10 AM starting next Monday to accommodate the new Berlin team members. Standups will remain 15 minutes and continue to be async-first.",
        "question": "What time will standups move to?",
        "answer": "10 AM",
    },
    {
        "context": "Project Nighthawk was officially approved by the executive team on March 5th with a budget of $2.4M and a target launch date of Q4 2026. The project aims to replace the legacy billing system.",
        "question": "What is the budget for Project Nighthawk?",
        "answer": "$2.4M",
    },
    {
        "context": "The team decided to deprecate the v1 API on September 30th. Customers have been notified and migration guides for v2 have been published. The v1 shutdown will be final with no further extensions.",
        "question": "When will the v1 API be deprecated?",
        "answer": "September 30th",
    },
    {
        "context": "Coda discovered that enabling SQLite WAL (Write-Ahead Logging) mode reduced read latency by 40% under concurrent load. This finding was shared with the infrastructure team and applied to all SQLite-backed services.",
        "question": "What optimization reduced SQLite read latency by 40%?",
        "answer": "WAL (Write-Ahead Logging) mode",
    },
    {
        "context": "The team adopted a trunk-based development workflow, replacing the previous Gitflow model. Feature flags will gate all unfinished work. This change takes effect immediately for all new branches.",
        "question": "What development workflow replaced Gitflow?",
        "answer": "trunk-based development",
    },
    {
        "context": "The design team finalized the new color palette: primary #1A1A2E, accent #E94560, background #16213E. The palette was reviewed for WCAG AA accessibility compliance before approval.",
        "question": "What is the primary color in the new design palette?",
        "answer": "#1A1A2E",
    },
    {
        "context": "The on-call rotation was updated to 5 people: Aria, Bex, Coda, Dev, and Eli. Each person is on-call for one week, rotating on Mondays at 09:00 UTC.",
        "question": "How long is each on-call rotation?",
        "answer": "one week",
    },
    {
        "context": "The team benchmarked three embedding models for semantic search: all-MiniLM-L6-v2 (fastest), all-mpnet-base-v2 (best quality), and paraphrase-multilingual-MiniLM-L12-v2 (multilingual). They chose all-MiniLM-L6-v2 for its speed and acceptable quality trade-off.",
        "question": "Which embedding model was chosen for semantic search?",
        "answer": "all-MiniLM-L6-v2",
    },
    {
        "context": "The incident report for INC-4821 concluded that the root cause was a misconfigured load balancer health check that was marking healthy instances as unhealthy, causing cascading failures across the cluster.",
        "question": "What was the root cause of INC-4821?",
        "answer": "misconfigured load balancer health check",
    },
    {
        "context": "The team agreed to freeze all non-critical merges after May 15th for the upcoming mobile release cut. Only P0 bug fixes and security patches are allowed during the freeze.",
        "question": "When does the merge freeze begin?",
        "answer": "May 15th",
    },
    {
        "context": "After a 6-month trial, the team decided to permanently adopt async code reviews via GitHub Discussions instead of synchronous PR review meetings. The async process reduced context-switching overhead by an estimated 35%.",
        "question": "What replaced synchronous PR review meetings?",
        "answer": "async code reviews via GitHub Discussions",
    },
]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def recall_at_k(results: list, answer: str, k: int) -> tuple[bool, int]:
    """Check if answer appears in top-k results. Returns (hit, rank)."""
    answer_lower = answer.lower()
    for i, r in enumerate(results[:k]):
        excerpt = (r.content_excerpt or "").lower()
        path = (r.path or "").lower()
        if answer_lower in excerpt or answer_lower in path:
            return True, i + 1
    return False, -1


def run_benchmark(
    data: list[dict],
    k: int = 5,
    limit: int | None = None,
    verbose: bool = False,
) -> dict:
    if limit:
        data = data[:limit]

    with tempfile.TemporaryDirectory() as tmp:
        ao = Ompa(vault_path=tmp, enable_semantic=True)

        # Index all context chunks
        print(f"Indexing {len(data)} context chunks...")
        t0 = time.perf_counter()
        for i, item in enumerate(data):
            ao.write(
                content=item["context"],
                tags=["benchmark"],
                file_path=f"work/benchmark/chunk-{i:04d}.md",
            )
        index_time = time.perf_counter() - t0
        ao.rebuild_index()

        # Query and measure
        print(f"Querying {len(data)} questions (k={k})...")
        hits = 0
        ranks = []
        query_times = []

        for item in data:
            qt0 = time.perf_counter()
            results = ao.search(item["question"], limit=k)
            query_times.append(time.perf_counter() - qt0)

            hit, rank = recall_at_k(results, item["answer"], k)
            if hit:
                hits += 1
                ranks.append(rank)

            if verbose:
                status = "✓" if hit else "✗"
                print(f"  [{status}] Q: {item['question'][:60]}...")
                if not hit:
                    print(f"       Expected: {item['answer']}")

        recall = hits / len(data)
        mean_rank = sum(ranks) / len(ranks) if ranks else -1
        mean_query_ms = (sum(query_times) / len(query_times)) * 1000

        return {
            "recall_at_k": recall,
            "k": k,
            "hits": hits,
            "total": len(data),
            "mean_rank": mean_rank,
            "index_time_s": round(index_time, 2),
            "mean_query_ms": round(mean_query_ms, 2),
        }


def main():
    parser = argparse.ArgumentParser(description="OMPA LongMemEval benchmark")
    parser.add_argument("--k", type=int, default=5, help="Recall@K (default: 5)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items")
    parser.add_argument("--data-path", type=str, default=None, help="Path to JSONL data file")
    parser.add_argument("--verbose", action="store_true", help="Show per-question results")
    args = parser.parse_args()

    # Load data
    if args.data_path:
        data_path = Path(args.data_path)
        with data_path.open() as f:
            data = [json.loads(line) for line in f if line.strip()]
        print(f"Loaded {len(data)} items from {data_path}")
    else:
        data = SAMPLE_DATA
        print(f"Using built-in sample dataset ({len(data)} items)")

    print()
    results = run_benchmark(data, k=args.k, limit=args.limit, verbose=args.verbose)

    print()
    print("=" * 50)
    print(f"  Recall@{results['k']}:     {results['recall_at_k']:.1%}  ({results['hits']}/{results['total']})")
    print(f"  Mean rank:      {results['mean_rank']:.1f}")
    print(f"  Index time:     {results['index_time_s']}s")
    print(f"  Mean query:     {results['mean_query_ms']:.1f}ms")
    print("=" * 50)

    return results


if __name__ == "__main__":
    main()
