"""Redis-backed token bucket rate limiter with an atomic Lua check-and-consume."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

import redis

from app.config import get_settings
from app.redis_client import get_redis

logger = logging.getLogger("apipartnerhub.ratelimit")
settings = get_settings()

# KEYS[1] = bucket hash; ARGV = capacity, refill_per_second, now_ms, cost, ttl_seconds.
# Returns {allowed (0|1), remaining_floor, seconds_until_full, retry_after_seconds}.
TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts) / 1000.0
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)

local reset_full = math.ceil((capacity - tokens) / rate)
local retry_after = 0
if allowed == 0 then
  retry_after = math.max(1, math.ceil((cost - tokens) / rate))
end
return {allowed, math.floor(tokens), reset_full, retry_after}
"""


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int
    retry_after: int

    def headers(self) -> dict[str, str]:
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_after),
        }


class TokenBucketLimiter:
    """Per-partner token bucket: steady refill = rpm, capacity = 2x rpm (burst)."""

    def check(self, partner_id: int, tier: str, rpm: int) -> RateLimitDecision:
        bucket_rpm = rpm if rpm > 0 else settings.RATE_LIMIT_DEFAULT_RPM
        capacity = max(1, bucket_rpm * 2)
        rate = bucket_rpm / 60.0
        ttl = max(60, math.ceil(capacity / rate) + 60)
        key = f"ratelimit:{tier}:{partner_id}"
        now_ms = int(time.time() * 1000)
        try:
            client = get_redis()
            script = client.register_script(TOKEN_BUCKET_LUA)
            raw = script(keys=[key], args=[capacity, rate, now_ms, 1, ttl])
        except redis.RedisError:
            logger.warning(
                "rate_limiter_redis_unavailable_fail_open",
                extra={"partner_id": partner_id, "tier": tier},
            )
            return RateLimitDecision(
                allowed=True,
                limit=capacity,
                remaining=capacity,
                reset_after=60,
                retry_after=0,
            )
        allowed = bool(int(raw[0]))
        return RateLimitDecision(
            allowed=allowed,
            limit=capacity,
            remaining=max(0, int(raw[1])),
            reset_after=max(0, int(raw[2])),
            retry_after=max(0, int(raw[3])),
        )


limiter = TokenBucketLimiter()
