"""Audit-memory overhead: write latency (audit off / on / HMAC / Ed25519),
KG fact-add latency, single-append throughput, verify time at 10k / 100k
entries, as_of replay time, and search latency with vs without citations.

All data is synthetic and generated here (seeded).  Modes are interleaved
round-robin across rounds so box-load drift hits every mode equally.

    python benchmarks/audit_overhead.py --out audit_overhead.json [--quick]
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from ompa import Ompa
from ompa.audit import AuditLog, HMACSigner, generate_key, load_key

logging.disable(logging.WARNING)


def pct(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {}
    return {
        "n": len(xs),
        "p50": statistics.median(xs),
        "p95": xs[min(len(xs) - 1, int(0.95 * len(xs)))],
        "mean": statistics.fmean(xs),
    }


def signer_for(mode: str, tmp: Path):
    if mode == "hmac":
        return HMACSigner(bytes(range(32)))
    if mode == "ed25519":
        try:
            return load_key(generate_key(tmp / f"k{random.random()}.pem"))
        except ImportError:
            return None
    return None


WORDS = [f"w{i}" for i in range(5000)]


def note_text(rng: random.Random, words: int = 120) -> str:
    links = " ".join(f"[[E{rng.randint(0, 300)}]]" for _ in range(3))
    return " ".join(rng.choices(WORDS, k=words)) + "\n" + links


def bench_writes(modes, n_writes: int, rounds: int, seed: int) -> dict:
    res = {m: {"write_ms": [], "kg_add_us": []} for m in modes}
    for r in range(rounds):
        order = modes[r % len(modes) :] + modes[: r % len(modes)]
        for m in order:
            tmp = Path(tempfile.mkdtemp(prefix=f"ompa-ov-{m}-"))
            try:
                kw = {"audit": m != "off"}
                if m in ("hmac", "ed25519"):
                    s = signer_for(m, tmp)
                    if s is None:
                        continue
                    kw["audit_key"] = s
                ao = Ompa(vault_path=str(tmp / "v"), enable_semantic=False, **kw)
                rng = random.Random(seed + r)
                for i in range(n_writes):
                    text = note_text(rng)
                    t0 = time.perf_counter()
                    ao.write(text, file_path=f"work/n{i}.md", tags=["t1", "t2"])
                    res[m]["write_ms"].append((time.perf_counter() - t0) * 1e3)
                for i in range(n_writes):
                    t0 = time.perf_counter()
                    ao.kg_add(f"S{i}", "rel", f"O{rng.randint(0, 999)}")
                    res[m]["kg_add_us"].append((time.perf_counter() - t0) * 1e6)
                if m != "off":
                    assert ao.audit_verify(drift=False)["ok"]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    out = {m: {k: pct(v) for k, v in d.items()} for m, d in res.items() if d["write_ms"]}
    base = out["off"]
    for d in out.values():
        d["write_overhead_ms_p50"] = d["write_ms"]["p50"] - base["write_ms"]["p50"]
        d["write_overhead_pct_p50"] = 100 * d["write_overhead_ms_p50"] / base["write_ms"]["p50"]
        d["kg_add_overhead_us_p50"] = d["kg_add_us"]["p50"] - base["kg_add_us"]["p50"]
    return out


def build_log(path: Path, n: int, signer, rng: random.Random) -> float:
    t0 = time.perf_counter()
    log = AuditLog(path, signer=signer)
    remaining = n - 1  # genesis
    while remaining > 0:
        with log.transaction():
            for _ in range(min(1000, remaining)):
                if rng.random() < 0.5:
                    body = {
                        "path": f"work/n{rng.randint(0, 9999)}.md",
                        "vault": "shared",
                        "sha256": f"{rng.getrandbits(256):064x}",
                        "bytes": rng.randint(100, 9000),
                    }
                    log.append("note.write", body)
                else:
                    body = {
                        "s": f"E{rng.randint(0, 5000)}",
                        "p": "links_to",
                        "o": f"E{rng.randint(0, 5000)}",
                        "valid_from": "2026-10-21",
                        "valid_to": None,
                        "confidence": 1.0,
                        "source": f"/v/work/n{rng.randint(0, 9999)}.md",
                        "source_line": rng.randint(1, 80),
                        "source_sha256": f"{rng.getrandbits(256):064x}",
                    }
                    log.append("fact.add", body)
                remaining -= 1
    log.close()
    return time.perf_counter() - t0


def bench_verify(sizes, modes, repeats: int, seed: int) -> dict:
    out = {}
    for n in sizes:
        for m in modes:
            tmp = Path(tempfile.mkdtemp(prefix=f"ompa-vf-{n}-{m}-"))
            try:
                signer = signer_for(m, tmp) if m != "unsigned" else None
                if m != "unsigned" and signer is None:
                    continue
                build_s = build_log(tmp / "audit.sqlite3", n, signer, random.Random(seed))
                size = sum(p.stat().st_size for p in tmp.glob("audit.sqlite3*"))
                times = []
                for _ in range(repeats):
                    log = AuditLog(tmp / "audit.sqlite3", readonly=True)
                    t0 = time.perf_counter()
                    rep = log.verify(verifier=signer)
                    times.append(time.perf_counter() - t0)
                    assert rep.ok and rep.entries == n, rep.issues[:3]
                    log.close()
                log = AuditLog(tmp / "audit.sqlite3", signer=signer)
                t0 = time.perf_counter()
                b = log.export_bundle(start=max(1, n - 999), proofs_for=[n - 500])
                export_s = time.perf_counter() - t0
                t0 = time.perf_counter()
                from ompa.governance import PointInTimeView

                with PointInTimeView(log, "2999-01-01") as view:
                    replayed = view.entries_replayed
                asof_s = time.perf_counter() - t0
                log.close()
                out[f"{m}_{n}"] = {
                    "entries": n,
                    "mode": m,
                    "build_seconds_batched_1000": build_s,
                    "verify_seconds": times,
                    "verify_seconds_median": statistics.median(times),
                    "verify_us_per_entry": 1e6 * statistics.median(times) / n,
                    "db_bytes": size,
                    "bytes_per_entry": size / n,
                    "export_1000_seconds": export_s,
                    "bundle_bytes": len(json.dumps(b)),
                    "as_of_full_replay_seconds": asof_s,
                    "as_of_entries_replayed": replayed,
                }
                print(n, m, out[f"{m}_{n}"]["verify_seconds_median"], flush=True)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    return out


def bench_append(n: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="ompa-ap-"))
    try:
        log = AuditLog(tmp / "audit.sqlite3")
        lat = []
        for i in range(n):
            t0 = time.perf_counter()
            log.append("note.write", {"path": f"n{i}.md", "sha256": "ab" * 32})
            lat.append((time.perf_counter() - t0) * 1e6)
        return {"single_append_us": pct(lat), "appends_per_s": 1e6 / statistics.fmean(lat)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_search_citations(n_notes: int, n_queries: int, seed: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="ompa-sc-"))
    try:
        rng = random.Random(seed)
        v = tmp / "v"
        (v / "work").mkdir(parents=True)
        for i in range(n_notes):
            (v / "work" / f"n{i}.md").write_text(note_text(rng, 200))
        ao = Ompa(vault_path=str(v), enable_semantic=False, audit=True)
        ao.search("warm up", 5)
        qs = [" ".join(rng.choices(WORDS, k=3)) for _ in range(n_queries)]
        res = {"with": [], "without": []}
        for i, q in enumerate(qs):
            for c in (True, False) if i % 2 == 0 else (False, True):
                t0 = time.perf_counter()
                ao.search(q, 5, citations=c)
                res["with" if c else "without"].append((time.perf_counter() - t0) * 1e3)
        out = {k: pct(v) for k, v in res.items()}
        out["notes"] = n_notes
        out["overhead_ms_p50"] = out["with"]["p50"] - out["without"]["p50"]
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--only", default="writes,append,verify,search")
    a = ap.parse_args()
    only = set(a.only.split(","))
    R: dict = {"seed": a.seed, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if "writes" in only:
        R["writes"] = bench_writes(
            ["off", "on", "hmac", "ed25519"], 60 if a.quick else 300, 2 if a.quick else 3, a.seed
        )
        print(
            "writes",
            json.dumps({m: d["write_ms"]["p50"] for m, d in R["writes"].items()}),
            flush=True,
        )
    if "append" in only:
        R["append"] = bench_append(500 if a.quick else 5000)
        print("append", R["append"]["appends_per_s"], flush=True)
    if "verify" in only:
        sizes = [10_000] if a.quick else [10_000, 100_000]
        R["verify"] = bench_verify(
            sizes, ["unsigned", "hmac", "ed25519"], 1 if a.quick else 3, a.seed
        )
    if "search" in only:
        R["search_citations"] = [
            bench_search_citations(n, 60 if a.quick else 200, a.seed)
            for n in ([500] if a.quick else [1000, 10000])
        ]
        print(
            "search",
            [(d["notes"], d["overhead_ms_p50"]) for d in R["search_citations"]],
            flush=True,
        )
    R["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    a.out.write_text(json.dumps(R, indent=1))


if __name__ == "__main__":
    main()
