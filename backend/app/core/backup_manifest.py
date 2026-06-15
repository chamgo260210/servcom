from __future__ import annotations

import enum
import hashlib
import json
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

SCHEMA_VERSION = "1.0.0"
SUPPORTED_DOMAINS = {"VISITORS", "SERIALS"}
BACKUP_DOMAINS = {"VISITORS", "SERIALS", "FULL", "WORK"}
SERVER_RESTORE_DOMAINS = {"VISITORS", "SERIALS", "FULL", "WORK"}
UPLOAD_RESTORE_DOMAINS = {"VISITORS", "SERIALS"}
PHASE1_REJECTED_DOMAINS = set()

VISITOR_TABLES = [
    "visitor_school_years",
    "visitor_periods",
    "visitor_daily_counts",
    "visitor_running_totals",
    "visitor_monthly_stats",
    "visitor_period_stats",
    "visitor_year_stats",
]

SERIAL_TABLES = [
    "serial_layouts",
    "serial_shelf_types",
    "serial_shelves",
    "serial_publications",
]

FULL_TABLES = [
    "users",
    "auth_accounts",
    "shifts",
    "user_shifts",
    "shift_requests",
    "notices",
    "notice_targets",
    "notice_reads",
    *VISITOR_TABLES,
    *SERIAL_TABLES,
]

WORK_TABLES = [
    "users",
    "shifts",
    "user_shifts",
    "shift_requests",
    "audit_logs",
]


def serialize_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    return value


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=serialize_value)


def calculate_checksum(meta: dict, data: dict) -> str:
    return hashlib.sha256(canonical_json({"meta": meta, "data": data}).encode("utf-8")).hexdigest()
