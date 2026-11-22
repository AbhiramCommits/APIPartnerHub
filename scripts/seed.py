"""Seed a deterministic demo dataset and print partner credentials exactly once.

Usage:
    python -m scripts.seed
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, text

from app.db import SessionLocal, engine, init_db
from app.models import ApiKey, Campaign, IdempotencyRecord, MetricDaily, OAuthClient, Partner
from app.security import generate_api_key, generate_oauth_client, scopes_to_csv

SEED = 42
DAYS_OF_METRICS = 90
TOTAL_CAMPAIGNS = 25
CREDENTIALS_FILE = Path(".seed_credentials.json")

PARTNERS = [
    ("Acme Retail", "free", 60),
    ("Globex Media", "standard", 600),
    ("Initech Global", "enterprise", 3000),
]

SCOPES_BY_TIER = {
    "free": ["partner:read", "campaigns:read", "reports:read"],
    "standard": ["partner:read", "campaigns:read", "campaigns:write", "reports:read", "bulk:write"],
    "enterprise": [
        "partner:read",
        "campaigns:read",
        "campaigns:write",
        "reports:read",
        "bulk:write",
    ],
}

STATUSES = ["active", "paused", "ended", "active", "active"]
OBJECTIVES = ["awareness", "consideration", "conversions", "retention"]
OBJECTIVE_LABELS = {
    "awareness": "Brand Awareness",
    "consideration": "Consideration Push",
    "conversions": "Conversion Blitz",
    "retention": "Retention Wave",
}


def reset_tables() -> None:
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE TABLE idempotency_records, metrics_daily, campaigns, api_keys, "
                    "oauth_clients, partners RESTART IDENTITY CASCADE"
                )
            )
        return
    with SessionLocal() as session:
        session.execute(delete(IdempotencyRecord))
        session.execute(delete(MetricDaily))
        session.execute(delete(Campaign))
        session.execute(delete(ApiKey))
        session.execute(delete(OAuthClient))
        session.execute(delete(Partner))
        session.commit()


def seed() -> dict[str, object]:
    init_db()
    reset_tables()
    rng = random.Random(SEED)
    now = datetime.now(UTC)
    today = date.today()
    credentials: list[dict[str, object]] = []

    with SessionLocal() as session:
        partners: list[Partner] = []
        for name, tier, rpm in PARTNERS:
            partner = Partner(name=name, tier=tier, rate_limit_rpm=rpm)
            session.add(partner)
            session.flush()
            partners.append(partner)

            prefix, plaintext, key_hash = generate_api_key()
            client_id, client_secret, client_hash = generate_oauth_client()
            scopes = SCOPES_BY_TIER[tier]
            session.add(
                ApiKey(
                    partner_id=partner.id,
                    prefix=prefix,
                    key_hash=key_hash,
                    scopes=scopes_to_csv(scopes),
                )
            )
            session.add(
                OAuthClient(
                    partner_id=partner.id,
                    client_id=client_id,
                    client_secret_hash=client_hash,
                    scopes=scopes_to_csv(scopes),
                )
            )
            credentials.append(
                {
                    "partner_id": partner.id,
                    "partner_name": partner.name,
                    "tier": tier,
                    "rate_limit_rpm": rpm,
                    "api_key": plaintext,
                    "api_key_prefix": prefix,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scopes": scopes,
                }
            )

        campaigns: list[Campaign] = []
        for index in range(TOTAL_CAMPAIGNS):
            partner = partners[index % len(partners)]
            objective = OBJECTIVES[index % len(OBJECTIVES)]
            status = STATUSES[index % len(STATUSES)]
            created_at = now - timedelta(days=rng.randint(1, 120), minutes=index)
            campaign = Campaign(
                partner_id=partner.id,
                name=f"{partner.name.split()[0]} {OBJECTIVE_LABELS[objective]} {index + 1:02d}",
                status=status,
                daily_budget_cents=rng.randrange(5_000, 250_001, 500),
                objective=objective,
                created_at=created_at,
                updated_at=created_at + timedelta(hours=rng.randint(0, 72)),
            )
            session.add(campaign)
            campaigns.append(campaign)
        session.flush()

        metric_rows = 0
        for campaign in campaigns:
            daily_budget = campaign.daily_budget_cents
            base_impressions = max(500, daily_budget // rng.randint(2, 8))
            for day_offset in range(DAYS_OF_METRICS):
                metric_date = today - timedelta(days=DAYS_OF_METRICS - 1 - day_offset)
                weekday_factor = 0.75 if metric_date.weekday() >= 5 else 1.0
                impressions = int(base_impressions * rng.uniform(0.7, 1.35) * weekday_factor)
                ctr = rng.uniform(0.008, 0.035)
                clicks = max(1, int(impressions * ctr))
                cpc_cents = rng.uniform(35, 180)
                spend_cents = min(daily_budget, max(100, int(clicks * cpc_cents)))
                conversions = max(0, int(clicks * rng.uniform(0.02, 0.09)))
                session.add(
                    MetricDaily(
                        campaign_id=campaign.id,
                        date=metric_date,
                        impressions=impressions,
                        clicks=clicks,
                        spend_cents=spend_cents,
                        conversions=conversions,
                    )
                )
                metric_rows += 1

        session.commit()

        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "partners": credentials,
            "campaigns": len(campaigns),
            "metric_rows": metric_rows,
        }

    CREDENTIALS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    CREDENTIALS_FILE.chmod(0o600)
    return payload


def print_credentials(payload: dict[str, object]) -> None:
    partners = payload["partners"]
    assert isinstance(partners, list)
    print()
    print("=" * 100)
    print("  Seed complete - credentials shown ONCE (also in .seed_credentials.json)")
    print("=" * 100)
    for entry in partners:
        assert isinstance(entry, dict)
        print(f"\nPartner #{entry['partner_id']}  {entry['partner_name']}  [{entry['tier']}]")
        print(
            f"  rate limit : {entry['rate_limit_rpm']} req/min "
            f"(burst {2 * int(entry['rate_limit_rpm'])})"
        )
        print(f"  scopes     : {', '.join(entry['scopes'])}")
        print(f"  X-API-Key  : {entry['api_key']}")
        print(f"  client_id  : {entry['client_id']}")
        print(f"  client_secret: {entry['client_secret']}")
    print()
    print(
        f"Seeded {payload['campaigns']} campaigns and {payload['metric_rows']} daily metric rows "
        f"(seed={SEED}, {DAYS_OF_METRICS} days/campaign)."
    )
    print("=" * 100)


if __name__ == "__main__":
    result = seed()
    print_credentials(result)
    sys.exit(0)
