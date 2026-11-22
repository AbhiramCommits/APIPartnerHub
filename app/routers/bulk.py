"""CSV bulk campaign import and template download."""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.auth import AuthContext, require_scopes
from app.db import get_session
from app.errors import ValidationFailed
from app.models import Campaign, utcnow
from app.schemas import BulkResult, BulkRowError

router = APIRouter(tags=["Bulk"])

REQUIRED_COLUMNS = ["name", "status", "daily_budget_cents", "objective"]
VALID_STATUSES = {"active", "paused", "ended"}
VALID_OBJECTIVES = {"awareness", "consideration", "conversions", "retention"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post(
    "/bulk/campaigns",
    response_model=BulkResult,
    summary="Bulk create campaigns from CSV",
    description=(
        "Upload a UTF-8 CSV with columns `name,status,daily_budget_cents,objective`. Valid rows "
        "are created; invalid rows are reported with row numbers and field names. Returns 200 "
        "when every row is accepted and 207 when any row is rejected."
    ),
    responses={
        207: {"description": "Partial success: at least one row was rejected."},
        422: {"description": "Malformed header, empty file or oversized upload."},
    },
)
def bulk_campaigns(
    context: Annotated[AuthContext, Depends(require_scopes("bulk:write"))],
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(description="CSV file of campaigns to create.")],
) -> JSONResponse:
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationFailed(
            "CSV upload is too large.",
            details=[{"field": "file", "message": "Maximum upload size is 5 MB."}],
        )
    if not raw.strip():
        raise ValidationFailed(
            "CSV upload is empty.",
            details=[{"field": "file", "message": "Upload a file with a header row."}],
        )
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailed(
            "CSV must be UTF-8 encoded.",
            details=[{"field": "file", "message": "Re-export the file as UTF-8."}],
        ) from exc

    reader = csv.DictReader(io.StringIO(content))
    fieldnames = [str(name).strip() for name in (reader.fieldnames or [])]
    if not fieldnames:
        raise ValidationFailed(
            "CSV has no header row.",
            details=[
                {
                    "field": "header",
                    "message": f"Expected columns: {', '.join(REQUIRED_COLUMNS)}.",
                }
            ],
        )
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise ValidationFailed(
            f"CSV header is missing required column(s): {', '.join(missing)}.",
            details=[
                {"field": "header", "message": f"Required columns: {', '.join(REQUIRED_COLUMNS)}."}
            ],
        )
    reader.fieldnames = fieldnames

    errors: list[BulkRowError] = []
    accepted_campaigns: list[Campaign] = []
    rejected = 0
    for row_number, row in enumerate(reader, start=2):
        normalized = {str(key).strip(): (value or "").strip() for key, value in row.items() if key}
        if not any(normalized.values()):
            continue
        row_errors = _validate_row(normalized)
        if row_errors:
            rejected += 1
            errors.extend(BulkRowError(row=row_number, **item) for item in row_errors)
            continue
        accepted_campaigns.append(
            Campaign(
                partner_id=context.partner.id,
                name=normalized["name"],
                status=normalized["status"],
                daily_budget_cents=int(normalized["daily_budget_cents"]),
                objective=normalized["objective"],
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        session.add(accepted_campaigns[-1])

    session.flush()
    created_ids = [campaign.id for campaign in accepted_campaigns]
    session.commit()

    result = BulkResult(
        accepted=len(accepted_campaigns),
        rejected=rejected,
        errors=errors,
        created_ids=created_ids,
    )
    status_code = 207 if rejected else 200
    return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))


def _validate_row(row: dict[str, str]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    name = row.get("name", "")
    if not name:
        errors.append({"field": "name", "message": "name is required"})
    elif len(name) > 200:
        errors.append({"field": "name", "message": "name must be at most 200 characters"})

    status = row.get("status", "")
    if status not in VALID_STATUSES:
        errors.append({"field": "status", "message": "status must be active, paused or ended"})

    objective = row.get("objective", "")
    if objective not in VALID_OBJECTIVES:
        errors.append(
            {
                "field": "objective",
                "message": "objective must be awareness, consideration, conversions or retention",
            }
        )

    budget = row.get("daily_budget_cents", "")
    try:
        budget_value = int(budget)
    except ValueError:
        errors.append(
            {
                "field": "daily_budget_cents",
                "message": "daily_budget_cents must be a non-negative integer",
            }
        )
    else:
        if budget_value < 0:
            errors.append(
                {
                    "field": "daily_budget_cents",
                    "message": "daily_budget_cents must be a non-negative integer",
                }
            )
    return errors


@router.get(
    "/bulk/campaigns/template.csv",
    summary="Download the campaign CSV template",
    description="Returns the exact header row accepted by the bulk import endpoint.",
    response_class=Response,
    responses={
        200: {
            "description": "CSV template.",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
def campaign_template(
    context: Annotated[AuthContext, Depends(require_scopes("bulk:write"))],
) -> Response:
    header = ",".join(REQUIRED_COLUMNS) + "\n"
    return Response(
        content=header,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="campaign_import_template.csv"'},
    )
