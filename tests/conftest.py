"""Hermetic test fixtures: SQLite temp database, fakeredis and an ASGI client.

No Postgres or Redis service is required. Environment variables are set before importing the
application so ``app.db`` builds a SQLite engine and the limiter talks to fakeredis.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

TMP_DIR = tempfile.mkdtemp(prefix="apipartnerhub-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DIR}/test.db"
os.environ["REDIS_URL"] = "redis://localhost:6399/0"
os.environ["JWT_SECRET"] = "test-secret-e3b0c44298fc1c149afbf4c8996fb924"
os.environ["ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["RATE_LIMIT_DEFAULT_RPM"] = "60"

import fakeredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402

from app import auth as auth_module  # noqa: E402
from app import observability  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import ApiKey, Campaign, MetricDaily, OAuthClient, Partner, utcnow  # noqa: E402
from app.redis_client import set_redis_client  # noqa: E402
from app.security import ALL_SCOPES, generate_api_key, generate_oauth_client, scopes_to_csv  # noqa: E402


@dataclass(slots=True)
class SeedPartner:
    id: int
    name: str
    tier: str
    rpm: int
    api_key: str
    client_id: str
    client_secret: str
    scopes: list[str]

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


@pytest.fixture(autouse=True)
def clean_state() -> Any:
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    set_redis_client(redis_client)  # type: ignore[arg-type]
    auth_module._VERIFIED_CACHE.clear()
    observability.reset()
    yield
    redis_client.flushall()


@pytest.fixture
async def client() -> Any:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
def make_partner() -> Callable[..., SeedPartner]:
    def factory(
        name: str = "Test Partner",
        *,
        tier: str = "enterprise",
        rpm: int | None = None,
        scopes: list[str] | None = None,
    ) -> SeedPartner:
        effective_rpm = rpm if rpm is not None else 10_000
        granted = scopes if scopes is not None else list(ALL_SCOPES)
        prefix, api_key, key_hash = generate_api_key()
        client_id, client_secret, client_hash = generate_oauth_client()
        with SessionLocal() as session:
            partner = Partner(name=name, tier=tier, rate_limit_rpm=effective_rpm)
            session.add(partner)
            session.flush()
            session.add(
                ApiKey(
                    partner_id=partner.id,
                    prefix=prefix,
                    key_hash=key_hash,
                    scopes=scopes_to_csv(granted),
                )
            )
            session.add(
                OAuthClient(
                    partner_id=partner.id,
                    client_id=client_id,
                    client_secret_hash=client_hash,
                    scopes=scopes_to_csv(granted),
                )
            )
            session.commit()
            return SeedPartner(
                id=partner.id,
                name=name,
                tier=tier,
                rpm=effective_rpm,
                api_key=api_key,
                client_id=client_id,
                client_secret=client_secret,
                scopes=granted,
            )

    return factory


@pytest.fixture
def seed_campaign() -> Callable[..., int]:
    def factory(
        partner_id: int,
        name: str = "Campaign",
        *,
        status: str = "active",
        objective: str = "conversions",
        daily_budget_cents: int = 5000,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> int:
        timestamp = created_at or datetime.now(UTC)
        with SessionLocal() as session:
            campaign = Campaign(
                partner_id=partner_id,
                name=name,
                status=status,
                daily_budget_cents=daily_budget_cents,
                objective=objective,
                created_at=timestamp,
                updated_at=updated_at or timestamp,
            )
            session.add(campaign)
            session.commit()
            return campaign.id

    return factory


@pytest.fixture
def seed_metric() -> Callable[..., None]:
    def factory(
        campaign_id: int,
        metric_date: date,
        *,
        impressions: int,
        clicks: int,
        spend_cents: int,
        conversions: int,
    ) -> None:
        with SessionLocal() as session:
            session.add(
                MetricDaily(
                    campaign_id=campaign_id,
                    date=metric_date,
                    impressions=impressions,
                    clicks=clicks,
                    spend_cents=spend_cents,
                    conversions=conversions,
                )
            )
            session.commit()

    return factory


def revoke_key(api_key: str) -> None:
    from app.security import parse_api_key

    parsed = parse_api_key(api_key)
    assert parsed is not None
    prefix, _ = parsed
    with SessionLocal() as session:
        key = session.query(ApiKey).filter(ApiKey.prefix == prefix).one()
        key.revoked_at = utcnow()
        session.commit()
