"""CSV bulk import: success, partial success, malformed input and template."""

from __future__ import annotations

from typing import Any

from app.db import SessionLocal
from app.models import Campaign
from tests.conftest import SeedPartner

HEADER = "name,status,daily_budget_cents,objective\n"


def upload_payload(content: str | bytes) -> dict[str, Any]:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return {"file": ("campaigns.csv", data, "text/csv")}


async def test_good_csv_is_fully_accepted(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    content = HEADER + (
        "Spring A,active,1000,awareness\n"
        "Spring B,paused,2000,consideration\n"
        "Spring C,ended,3000,conversions\n"
    )
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(content)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 3
    assert body["rejected"] == 0
    assert len(body["created_ids"]) == 3
    with SessionLocal() as session:
        assert session.query(Campaign).filter(Campaign.partner_id == partner.id).count() == 3


async def test_partial_success_returns_207_with_row_errors(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    content = HEADER + (
        "Good One,active,1000,awareness\n"
        "Bad Status,launched,1000,awareness\n"
        "Good Two,paused,2000,conversions\n"
        "Bad Budget,active,abc,conversions\n"
    )
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(content)
    )
    assert response.status_code == 207
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 2
    assert len(body["created_ids"]) == 2
    errors = {(error["row"], error["field"]) for error in body["errors"]}
    assert errors == {(3, "status"), (5, "daily_budget_cents")}


async def test_all_rejected_still_reports_rows(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    content = HEADER + "Bad,launched,not-a-number,loyalty\n"
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(content)
    )
    assert response.status_code == 207
    body = response.json()
    assert body["accepted"] == 0
    assert body["rejected"] == 1
    assert {error["field"] for error in body["errors"]} == {
        "status",
        "daily_budget_cents",
        "objective",
    }


async def test_malformed_header(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post(
        "/v1/bulk/campaigns",
        headers=partner.headers,
        files=upload_payload("name,status\nFoo,active\n"),
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VAL-001"
    assert error["details"][0]["field"] == "header"
    assert "objective" in error["message"]


async def test_empty_upload(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(b"")
    )
    assert response.status_code == 422
    assert "empty" in response.json()["error"]["message"]


async def test_non_utf8_upload(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post(
        "/v1/bulk/campaigns",
        headers=partner.headers,
        files=upload_payload(b"\xff\xfe\x00\x00bad bytes"),
    )
    assert response.status_code == 422
    assert "UTF-8" in response.json()["error"]["message"]


async def test_missing_file(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post("/v1/bulk/campaigns", headers=partner.headers)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "validation_failed"


async def test_oversized_upload(client: Any, make_partner: Any) -> None:
    from app.routers.bulk import MAX_UPLOAD_BYTES

    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post(
        "/v1/bulk/campaigns",
        headers=partner.headers,
        files=upload_payload(b"x" * (MAX_UPLOAD_BYTES + 1)),
    )
    assert response.status_code == 422
    assert "too large" in response.json()["error"]["message"]


async def test_bom_only_upload_has_no_header(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(b"\xef\xbb\xbf")
    )
    assert response.status_code == 422
    assert "no header" in response.json()["error"]["message"]


async def test_row_level_name_and_budget_validation(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    long_name = "L" * 201
    content = HEADER + (
        f"{long_name},active,1000,awareness\n"
        ",active,1000,awareness\n"
        ",,,\n"
        "Negative Budget,active,-10,awareness\n"
    )
    response = await client.post(
        "/v1/bulk/campaigns", headers=partner.headers, files=upload_payload(content)
    )
    assert response.status_code == 207
    body = response.json()
    assert body["accepted"] == 0
    assert body["rejected"] == 3
    errors = {(error["row"], error["field"]) for error in body["errors"]}
    assert (2, "name") in errors
    assert (3, "name") in errors
    assert (5, "daily_budget_cents") in errors


async def test_template_download(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["bulk:write"])
    response = await client.get("/v1/bulk/campaigns/template.csv", headers=partner.headers)
    assert response.status_code == 200
    assert response.text == "name,status,daily_budget_cents,objective\n"
    assert "attachment" in response.headers["Content-Disposition"]


async def test_scope_required(client: Any, make_partner: Any) -> None:
    partner: SeedPartner = make_partner(scopes=["campaigns:read"])
    response = await client.post(
        "/v1/bulk/campaigns",
        headers=partner.headers,
        files=upload_payload(HEADER + "Row,active,1,awareness\n"),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE-001"
