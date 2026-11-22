"""Turn raw benchmark JSON into bench/results/RESULTS.md, CSV and a chart.

Every number written here is read from the raw JSON produced by ``bench.run_bench``; nothing
is estimated or edited by hand.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"


def load(name: str) -> Any:
    path = RESULTS / name
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run `make bench` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value: Any, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def build_markdown(
    environment: dict[str, Any],
    latency: list[dict[str, Any]],
    rate_limit: dict[str, Any],
    retry: dict[str, Any],
    csv_results: list[dict[str, Any]],
    auth: dict[str, Any],
) -> str:
    lines: list[str] = []
    add = lines.append

    add("# APIPartnerHub benchmark results")
    add("")
    add("All figures below are measured by `bench/run_bench.py` against the real Postgres 16 + ")
    add("Redis 7 stack with the seeded dataset. The harness starts a 4-worker uvicorn, warms up ")
    add("each endpoint, then records every response it observed. `bench/analyze.py` only reads ")
    add("the raw JSON written by that run.")
    add("")
    add("## Environment")
    add("")
    add(
        f"- Machine: **{environment['machine']}** "
        f"({environment['machine_arch']}), {environment['cpu_count']} logical CPUs"
    )
    add(f"- OS: `{environment['platform']}`")
    add(
        f"- Python **{environment['python_version']}**, PostgreSQL "
        f"**{environment['postgres_version']}**, Redis **{environment['redis_version']}**"
    )
    add(
        f"- uvicorn **{environment['uvicorn_workers']} workers**, load-generator concurrency "
        f"**{environment['concurrency']}**, **{environment['duration_seconds_per_endpoint']}s** "
        f"per endpoint"
    )
    add(
        f"- Seed: **{environment['seeded_campaigns']} campaigns**, "
        f"**{environment['seeded_metric_rows']} daily metric rows**"
    )
    add(f"- Base URL: `{environment['base_url']}`")
    add(f"- Run timestamp (UTC): `{environment['timestamp']}`")
    add("")

    add("## A. Latency and throughput")
    add("")
    add("Enterprise tier (3000 rpm / 6000 burst) so the limiter is not the bottleneck. ")
    add("Each endpoint ran for the stated duration after a warm-up.")
    add("")
    add("| Endpoint | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Error % |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in latency:
        add(
            f"| `{row['method']} {row['path']}` | {fmt(row['requests'])} | {fmt(row['rps'])} | "
            f"{fmt(row['p50_ms'])} | {fmt(row['p95_ms'])} | {fmt(row['p99_ms'])} | "
            f"{fmt(row['max_ms'])} | {fmt(row['error_rate_pct'])} |"
        )
    add("")
    add(
        "Error % counts responses outside the endpoint's expected status (or transport "
        "exceptions) divided by requests."
    )
    add("")

    add("## B. Rate-limit enforcement accuracy")
    add("")
    add(
        f"Fired **{rate_limit['requests_fired']}** simultaneous requests at the free-tier key "
        f"(steady **{rate_limit['steady_rpm']} rpm**, "
        f"burst capacity **{rate_limit['configured_burst_capacity']}**)."
    )
    add("")
    add("| Metric | Value |")
    add("|---|---:|")
    add(f"| Configured burst capacity | {rate_limit['configured_burst_capacity']} |")
    add(f"| Allowed (200) | {rate_limit['allowed']} |")
    add(f"| Rate limited (429) | {rate_limit['rate_limited']} |")
    add(f"| Other failures | {rate_limit['other_failures']} |")
    add(f"| Observation window | {fmt(rate_limit['window_seconds'], 3)} s |")
    add(
        f"| Tokens refilled during window (theoretical) | "
        f"{fmt(rate_limit['refill_tokens_during_window'])} |"
    )
    add(f"| Absolute deviation (allowed − capacity) | {rate_limit['absolute_deviation']:+d} |")
    add(f"| Deviation | {rate_limit['percent_deviation']:+.2f}% |")
    add(
        f"| 429 responses with valid Retry-After | {rate_limit['retry_after_valid_count']}/"
        f"{rate_limit['rate_limited']} |"
    )
    add(f"| Retry-After values observed (s) | {rate_limit['retry_after_samples']} |")
    add(
        f"| 200 responses with all RateLimit headers | "
        f"{rate_limit['rate_limit_headers_present_on_200']}/{rate_limit['allowed']} |"
    )
    add(
        f"| 429 bodies using the `rate_limited` envelope | "
        f"{rate_limit['rate_limited_envelope_count']}/{rate_limit['rate_limited']} |"
    )
    add("")
    add(
        "The token bucket is refilled continuously, so a fast burst can legitimately allow the "
        "theoretical refill shown above on top of the initial capacity."
    )
    add("")

    add("## C. SDK retry recovery")
    add("")
    add(
        "The free-tier bucket was drained, then the same Python SDK was driven with retries "
        f"**off** and **on** for {retry['requests']} sequential requests each."
    )
    add("")
    add("| Metric | Without retries | With retries |")
    add("|---|---:|---:|")
    add(
        f"| Successful responses | {retry['no_retry_success']}/{retry['requests']} "
        f"({fmt(retry['no_retry_success_rate_pct'])}%) | "
        f"{retry['retry_success']}/{retry['requests']} "
        f"({fmt(retry['retry_success_rate_pct'])}%) |"
    )
    add(
        f"| Mean request latency | {fmt(retry['no_retry_mean_ms'])} ms | "
        f"{fmt(retry['retry_mean_ms'])} ms |"
    )
    add(f"| Total retries performed | — | {retry['total_retries']} |")
    add(f"| Baseline mean latency (fresh bucket) | {fmt(retry['baseline_mean_ms'])} ms | — |")
    add(f"| Mean added latency vs baseline | — | {fmt(retry['mean_added_latency_ms'])} ms |")
    add("")
    add(
        f"Exhaustion phase allowed {retry['exhaustion_allowed_before_429']} requests before "
        "returning 429s; retries slept for the server-provided Retry-After."
    )
    add("")

    add("## D. CSV bulk import throughput")
    add("")
    add("| Rows | Seconds | Rows/sec | Accepted | Rejected | HTTP |")
    add("|---:|---:|---:|---:|---:|---:|")
    for row in csv_results:
        add(
            f"| {fmt(row['rows'])} | {fmt(row['seconds'], 3)} | {fmt(row['rows_per_sec'])} | "
            f"{fmt(row['accepted'])} | {fmt(row['rejected'])} | {row['http_status']} |"
        )
    add("")
    add(
        "Each file deliberately contains 2% invalid rows (every 50th row has status "
        "`launched`) so the import exercises per-row validation and the 207 partial-success path."
    )
    add("")

    add("## E. Auth-mode overhead")
    add("")
    add("Same endpoint (`GET /v1/partners/me`), enterprise tier, sequential requests.")
    add("")
    add("| Auth mode | Requests | Mean (ms) | p50 (ms) | p95 (ms) | Errors |")
    add("|---|---:|---:|---:|---:|---:|")
    for label, key in (("API key", "api_key"), ("OAuth bearer", "oauth")):
        row = auth[key]
        add(
            f"| {label} | {row['requests']} | {fmt(row['mean_ms'])} | {fmt(row['p50_ms'])} | "
            f"{fmt(row['p95_ms'])} | {row['errors']} |"
        )
    add("")
    delta = auth["mean_overhead_ms"]
    relation = f"{fmt(delta)} ms slower" if delta >= 0 else f"{fmt(abs(delta))} ms faster"
    add(
        f"API-key auth is **{relation}** per request on mean. Successful API-key "
        "verification is cached in-process for 30 s (the revocation flag is still read from "
        "Postgres on every request); OAuth verifies a JWT signature and performs a Redis "
        "`jti` lookup."
    )
    add("")

    add("## Reproduce")
    add("")
    add("```bash")
    add("docker compose up -d --wait")
    add("python -m scripts.seed")
    add("python -m bench.run_bench --port 8011 --workers 4 --concurrency 20 --duration 8")
    add("python -m bench.analyze")
    add("# or simply: make bench")
    add("```")
    add("")
    add("Raw observations: `bench/results/raw_environment.json`, `raw_latency.json`, ")
    add("`raw_rate_limit.json`, `raw_retry.json`, `raw_csv.json`, `raw_auth.json`. ")
    add("Per-endpoint CSV: `bench/results/latency_by_endpoint.csv`. ")
    add("Chart: `bench/results/latency_p95.png`.")
    add("")
    return "\n".join(lines)


def write_csv(latency: list[dict[str, Any]]) -> None:
    path = RESULTS / "latency_by_endpoint.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "endpoint",
                "requests",
                "duration_s",
                "rps",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "max_ms",
                "mean_ms",
                "error_rate_pct",
            ]
        )
        for row in latency:
            writer.writerow(
                [
                    f"{row['method']} {row['path']}",
                    row["requests"],
                    row["duration_s"],
                    row["rps"],
                    row["p50_ms"],
                    row["p95_ms"],
                    row["p99_ms"],
                    row["max_ms"],
                    row["mean_ms"],
                    row["error_rate_pct"],
                ]
            )
    print(f"wrote {path.relative_to(ROOT)}")


def write_chart(latency: list[dict[str, Any]]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping latency_p95.png")
        return

    labels = [f"{row['method']} {row['path']}" for row in latency]
    values = [row["p95_ms"] for row in latency]
    figure, axis = plt.subplots(figsize=(9, 4.2))
    axis.barh(labels, values, color="#2f6fdd")
    axis.set_xlabel("p95 latency (ms)")
    axis.set_title("APIPartnerHub per-endpoint p95 latency")
    axis.invert_yaxis()
    for index, value in enumerate(values):
        axis.text(value, index, f" {value:.1f}", va="center")
    figure.tight_layout()
    path = RESULTS / "latency_p95.png"
    figure.savefig(path, dpi=150)
    plt.close(figure)
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    environment = load("raw_environment.json")
    latency = load("raw_latency.json")
    rate_limit = load("raw_rate_limit.json")
    retry = load("raw_retry.json")
    csv_results = load("raw_csv.json")
    auth = load("raw_auth.json")

    markdown = build_markdown(environment, latency, rate_limit, retry, csv_results, auth)
    results_path = RESULTS / "RESULTS.md"
    results_path.write_text(markdown + "\n", encoding="utf-8")
    print(f"wrote {results_path.relative_to(ROOT)}")
    write_csv(latency)
    write_chart(latency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
