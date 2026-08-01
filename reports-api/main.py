"""reports-api — ClickHouse mart + S3/CDN cache-aside for prosthesis usage reports."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Any

import boto3
import httpx
from botocore.client import Config
from botocore.exceptions import ClientError
from clickhouse_driver import Client as CHClient
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

AUTH_URL = os.getenv("AUTH_URL", "http://bionicpro-auth:8001")
COOKIE_NAME = os.getenv("COOKIE_NAME", "bionicpro_session")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://10.2.67.21:3000")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "10.2.67.21")

CH_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CH_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CH_DB = os.getenv("CLICKHOUSE_DB", "bionicpro")

# S3 / MinIO
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET", "bionicpro-reports")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

# Public CDN base (Nginx)
CDN_PUBLIC_URL = os.getenv("CDN_PUBLIC_URL", f"http://{PUBLIC_HOST}:8088")

app = FastAPI(title="reports-api", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        FRONTEND_URL,
        f"http://{PUBLIC_HOST}:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ch() -> CHClient:
    return CHClient(
        host=CH_HOST,
        port=CH_PORT,
        user=CH_USER,
        password=CH_PASSWORD or "",
        database=CH_DB,
    )


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _object_key(username: str, start: date, end: date, watermark: date) -> str:
    """
    Versioned key: watermark in path → ETL bump = new object = CDN miss (invalidation).
    Structure for fast lookup by user + period.
    """
    return (
        f"reports/{username}/"
        f"{start.isoformat()}_{end.isoformat()}_wm-{watermark.isoformat()}.json"
    )


def _cdn_url(key: str) -> str:
    return f"{CDN_PUBLIC_URL.rstrip('/')}/cdn/{key}"


def _s3_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def _s3_put_json(key: str, body: dict[str, Any]) -> None:
    _s3().put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=3600",
    )


async def _current_user(request: Request) -> dict[str, Any]:
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{AUTH_URL}/auth/session",
            headers={"Cookie": f"{COOKIE_NAME}={session_id}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = resp.json()
    user = data.get("user") or {}
    username = user.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Missing username in session")
    return user


def _watermark() -> date | None:
    rows = _ch().execute(
        """
        SELECT max_source_date
        FROM etl_watermark FINAL
        WHERE pipeline = 'bionicpro_reports_etl'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    )
    if not rows:
        return None
    value = rows[0][0]
    if isinstance(value, datetime):
        return value.date()
    return value


def _build_report(username: str, start: date, end: date, wm: date) -> dict[str, Any]:
    rows = _ch().execute(
        """
        SELECT
            username,
            full_name,
            email,
            prosthesis_model,
            region,
            report_date,
            steps_count,
            active_minutes,
            battery_avg,
            load_cycles,
            fall_events,
            events_count
        FROM user_report_mart FINAL
        WHERE username = %(username)s
          AND report_date >= %(start)s
          AND report_date <= %(end)s
        ORDER BY report_date
        """,
        {"username": username, "start": start, "end": end},
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No report rows for user '{username}' in [{start} .. {end}]",
        )

    daily = [
        {
            "date": r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]),
            "steps_count": int(r[6]),
            "active_minutes": int(r[7]),
            "battery_avg": round(float(r[8]), 2),
            "load_cycles": int(r[9]),
            "fall_events": int(r[10]),
            "events_count": int(r[11]),
        }
        for r in rows
    ]

    summary = {
        "steps_count": sum(d["steps_count"] for d in daily),
        "active_minutes": sum(d["active_minutes"] for d in daily),
        "battery_avg": round(sum(d["battery_avg"] for d in daily) / len(daily), 2),
        "load_cycles": sum(d["load_cycles"] for d in daily),
        "fall_events": sum(d["fall_events"] for d in daily),
        "days": len(daily),
    }

    return {
        "user": {
            "username": rows[0][0],
            "full_name": rows[0][1],
            "email": rows[0][2],
            "prosthesis_model": rows[0][3],
            "region": rows[0][4],
        },
        "period": {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "etl_watermark": wm.isoformat(),
        },
        "summary": summary,
        "daily": daily,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": "clickhouse.bionicpro.user_report_mart",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/reports")
async def get_report(
    request: Request,
    date_from: date | None = Query(default=None, description="Inclusive start date"),
    date_to: date | None = Query(default=None, description="Inclusive end date"),
    user_id: str | None = Query(
        default=None,
        description="Ignored unless equal to current user (ACL)",
    ),
) -> JSONResponse:
    """
    Cache-aside:
      1) resolve period + watermark
      2) if object exists in S3 → return CDN URL (no ClickHouse hit)
      3) else generate from mart, put to S3, return CDN URL
    """
    user = await _current_user(request)
    username = user["username"]

    if user_id and user_id not in {username, user.get("sub")}:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only request your own report",
        )

    wm = _watermark()
    if wm is None:
        raise HTTPException(
            status_code=404,
            detail="Report data is not ready yet (ETL has not produced a watermark)",
        )

    end = date_to or wm
    start = date_from or (end - timedelta(days=13))

    if end > wm:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Requested period is not fully processed by Airflow yet. "
                f"Latest available date: {wm.isoformat()}"
            ),
        )
    if start > end:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")

    key = _object_key(username, start, end, wm)
    cdn_url = _cdn_url(key)

    if _s3_exists(key):
        return JSONResponse(
            content={
                "cache": "HIT",
                "storage": "s3",
                "cdn_url": cdn_url,
                "s3_key": key,
                "user": {"username": username},
                "period": {
                    "date_from": start.isoformat(),
                    "date_to": end.isoformat(),
                    "etl_watermark": wm.isoformat(),
                },
                "message": "Report served from S3 via CDN (ClickHouse not queried)",
            }
        )

    # MISS → generate from OLAP, store, return CDN link
    report = _build_report(username, start, end, wm)
    report["s3_key"] = key
    report["cdn_url"] = cdn_url
    _s3_put_json(key, report)

    return JSONResponse(
        content={
            "cache": "MISS",
            "storage": "s3",
            "cdn_url": cdn_url,
            "s3_key": key,
            "user": report["user"],
            "period": report["period"],
            "summary": report["summary"],
            "daily": report["daily"],
            "generated_at": report["generated_at"],
            "source": report["source"],
            "message": "Report generated from ClickHouse, stored in S3, available via CDN",
        }
    )
