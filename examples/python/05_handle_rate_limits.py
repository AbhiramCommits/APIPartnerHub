"""05 - drive the free tier into a 429 and demonstrate the retry contract."""

import asyncio
import time

from _credentials import base_url, partner
from apipartnerhub import APIPartnerHubClient, RateLimitedError, RetryPolicy


async def main() -> None:
    credentials = partner("free")
    no_retry = RetryPolicy(max_retries=0)
    semaphore = asyncio.Semaphore(30)
    timeline: list[tuple[float, int, float, str | None]] = []
    ok = 0
    limited = 0
    first_retry_after = 1.0
    started = time.perf_counter()

    async with APIPartnerHubClient(
        base_url(),
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
        retry_policy=no_retry,
    ) as client:

        async def one(index: int) -> None:
            nonlocal ok, limited, first_retry_after
            async with semaphore:
                try:
                    await client.get_me()
                    ok += 1
                except RateLimitedError as exc:
                    limited += 1
                    first_retry_after = exc.retry_after or 1.0
                    elapsed = (time.perf_counter() - started) * 1000
                    timeline.append((elapsed, index, exc.retry_after or 1.0, exc.request_id))

        await asyncio.gather(*(one(i) for i in range(1, 136)))

    print(f"fired 135 requests against a {credentials['rate_limit_rpm']} rpm tier")
    print(f"200 responses: {ok}   429 responses: {limited}")
    print("retry timeline (429s):")
    for elapsed, index, retry_after, request_id in sorted(timeline)[:6]:
        print(
            f"  #{index:03d} {elapsed:7.1f} ms -> 429 retry_after={retry_after:.1f}s "
            f"request_id={request_id}"
        )
    if len(timeline) > 6:
        print(f"  ... {len(timeline) - 6} more 429s")
    print(f"waiting {first_retry_after:.1f}s for one token, then retrying with backoff enabled")

    await asyncio.sleep(first_retry_after + 0.1)
    async with APIPartnerHubClient(
        base_url(),
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
    ) as retrying:
        me = await retrying.get_me()
        print(f"recovered: {me['name']} after {me.attempts} HTTP attempt(s)")


if __name__ == "__main__":
    asyncio.run(main())
