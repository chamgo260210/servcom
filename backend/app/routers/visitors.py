# File: /backend/app/routers/visitors.py
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from .. import models, schemas
from ..deps import get_db
from ..core.audit import record_log
from ..core.roles import get_current_user, require_role

router = APIRouter(prefix="/visitors", tags=["visitors"])


SEOUL_TZ = ZoneInfo("Asia/Seoul")
PERIOD_DEFAULTS = {
    models.VisitorPeriodType.SEMESTER_1: "1학기",
    models.VisitorPeriodType.SEMESTER_2: "2학기",
    models.VisitorPeriodType.SUMMER_BREAK: "여름방학",
    models.VisitorPeriodType.WINTER_BREAK: "겨울방학",
}

MAX_DAILY_VISITORS = 1_000_000
MAX_COUNTER_VALUE = 1_000_000
MAX_COUNTER_TOTAL = 100_000_000


def _today_seoul() -> date:
    return datetime.now(SEOUL_TZ).date()

def _default_year_dates(academic_year: int) -> tuple[date, date]:
    start_date = date(academic_year, 3, 1)
    end_day = calendar.monthrange(academic_year + 1, 2)[1]
    end_date = date(academic_year + 1, 2, end_day)
    return start_date, end_date


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first_weekday, _ = calendar.monthrange(year, month)
    offset = (weekday - first_weekday) % 7
    day = 1 + offset + (n - 1) * 7
    return date(year, month, day)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    last_date = date(year, month, last_day)
    offset = (last_date.weekday() - weekday) % 7
    return last_date - timedelta(days=offset)


def _clamp_range(start: date, end: date, year_start: date, year_end: date) -> tuple[date, date]:
    clamped_start = max(start, year_start)
    clamped_end = min(end, year_end)
    if clamped_end < clamped_start:
        clamped_end = clamped_start
    return clamped_start, clamped_end


def _default_period_ranges(academic_year: int, year_start: date, year_end: date) -> dict[models.VisitorPeriodType, tuple[date, date]]:
    summer_start = _nth_weekday(academic_year, 6, 0, 4)
    summer_end = _last_weekday(academic_year, 8, 4)
    winter_start = _nth_weekday(academic_year, 12, 0, 4)
    winter_end = _last_weekday(academic_year + 1, 2, 4)

    semester1_start = year_start
    semester1_end = summer_start - timedelta(days=1)
    semester2_start = summer_end + timedelta(days=1)
    semester2_end = winter_start - timedelta(days=1)

    ranges = {
        models.VisitorPeriodType.SEMESTER_1: (semester1_start, semester1_end),
        models.VisitorPeriodType.SUMMER_BREAK: (summer_start, summer_end),
        models.VisitorPeriodType.SEMESTER_2: (semester2_start, semester2_end),
        models.VisitorPeriodType.WINTER_BREAK: (winter_start, winter_end),
    }
    return {
        period_type: _clamp_range(start, end, year_start, year_end)
        for period_type, (start, end) in ranges.items()
    }


PERIOD_ORDER = [
    models.VisitorPeriodType.SEMESTER_1,
    models.VisitorPeriodType.SUMMER_BREAK,
    models.VisitorPeriodType.SEMESTER_2,
    models.VisitorPeriodType.WINTER_BREAK,
]

PERIOD_DETAIL_KEYS = {
    models.VisitorPeriodType.SEMESTER_1: "semester1",
    models.VisitorPeriodType.SUMMER_BREAK: "summer_break",
    models.VisitorPeriodType.SEMESTER_2: "semester2",
    models.VisitorPeriodType.WINTER_BREAK: "winter_break",
}


def _get_year(db: Session, year_id) -> models.VisitorSchoolYear:
    year = db.query(models.VisitorSchoolYear).filter(models.VisitorSchoolYear.id == year_id).first()
    if not year:
        raise HTTPException(status_code=404, detail="학년도 정보를 찾을 수 없습니다.")
    return year


def _ensure_within_year(year: models.VisitorSchoolYear, visit_date: date) -> None:
    if visit_date < year.start_date or visit_date > year.end_date:
        raise HTTPException(status_code=400, detail="학년도 기간 밖의 날짜입니다.")


def _ensure_non_negative(label: str, value: int | None, max_value: int) -> None:
    if value is None:
        return
    if value < 0:
        raise HTTPException(status_code=400, detail=f"{label}은(는) 0 이상이어야 합니다.")
    if value > max_value:
        raise HTTPException(status_code=400, detail=f"{label}은(는) {max_value:,} 이하만 입력할 수 있습니다.")


def _validate_daily_visitors(value: int | None) -> None:
    if value is None:
        raise HTTPException(status_code=400, detail="일일 방문자 수를 입력하세요.")
    _ensure_non_negative("일일 방문자 수", value, MAX_DAILY_VISITORS)


def _entry_calculation_source(entry: models.VisitorDailyCount) -> str:
    if all(getattr(entry, field) is not None for field in ("previous_total", "count1", "count2", "current_total")):
        return "COUNTER"
    return "NONE"


def _entry_out(entry: models.VisitorDailyCount, creator_name: str | None = None, updater_name: str | None = None) -> schemas.VisitorEntryOut:
    return schemas.VisitorEntryOut(
        id=entry.id,
        school_year_id=entry.school_year_id,
        visit_date=entry.visit_date,
        daily_visitors=entry.daily_visitors,
        previous_total=entry.previous_total,
        count1=entry.count1,
        count2=entry.count2,
        current_total=entry.current_total,
        calculation_source=_entry_calculation_source(entry),
        created_by=entry.created_by,
        updated_by=entry.updated_by,
        created_by_name=creator_name,
        updated_by_name=updater_name,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _get_running_total(db: Session, year: models.VisitorSchoolYear) -> models.VisitorRunningTotal:
    running = (
        db.query(models.VisitorRunningTotal)
        .filter(models.VisitorRunningTotal.school_year_id == year.id)
        .first()
    )
    if not running:
        running = models.VisitorRunningTotal(school_year_id=year.id)
        db.add(running)
        db.flush()
    return running


def _apply_entry_delta(
    db: Session,
    year: models.VisitorSchoolYear,
    visit_date: date,
    delta_visitors: int,
    delta_days: int,
) -> None:
    year_stat = (
        db.query(models.VisitorYearStat)
        .filter(models.VisitorYearStat.school_year_id == year.id)
        .first()
    )
    if not year_stat:
        year_stat = models.VisitorYearStat(school_year_id=year.id)
        db.add(year_stat)
        db.flush()
    year_stat.total_visitors = max(0, (year_stat.total_visitors or 0) + delta_visitors)
    year_stat.open_days = max(0, (year_stat.open_days or 0) + delta_days)

    monthly_stat = (
        db.query(models.VisitorMonthlyStat)
        .filter(
            models.VisitorMonthlyStat.school_year_id == year.id,
            models.VisitorMonthlyStat.year == visit_date.year,
            models.VisitorMonthlyStat.month == visit_date.month,
        )
        .first()
    )
    if not monthly_stat:
        monthly_stat = models.VisitorMonthlyStat(
            school_year_id=year.id,
            year=visit_date.year,
            month=visit_date.month,
        )
        db.add(monthly_stat)
        db.flush()
    monthly_stat.total_visitors = max(0, (monthly_stat.total_visitors or 0) + delta_visitors)
    monthly_stat.open_days = max(0, (monthly_stat.open_days or 0) + delta_days)

    period = (
        db.query(models.VisitorPeriod)
        .filter(
            models.VisitorPeriod.school_year_id == year.id,
            models.VisitorPeriod.start_date.isnot(None),
            models.VisitorPeriod.end_date.isnot(None),
            models.VisitorPeriod.start_date <= visit_date,
            models.VisitorPeriod.end_date >= visit_date,
        )
        .first()
    )
    if period:
        period_stat = (
            db.query(models.VisitorPeriodStat)
            .filter(
                models.VisitorPeriodStat.school_year_id == year.id,
                models.VisitorPeriodStat.period_id == period.id,
            )
            .first()
        )
        if not period_stat:
            period_stat = models.VisitorPeriodStat(
                school_year_id=year.id,
                period_id=period.id,
            )
            db.add(period_stat)
            db.flush()
        period_stat.total_visitors = max(0, (period_stat.total_visitors or 0) + delta_visitors)
        period_stat.open_days = max(0, (period_stat.open_days or 0) + delta_days)



def rebuild_visitor_stats(db: Session, year: models.VisitorSchoolYear) -> dict[str, int]:
    db.query(models.VisitorMonthlyStat).filter(models.VisitorMonthlyStat.school_year_id == year.id).delete(synchronize_session=False)
    db.query(models.VisitorPeriodStat).filter(models.VisitorPeriodStat.school_year_id == year.id).delete(synchronize_session=False)
    db.query(models.VisitorYearStat).filter(models.VisitorYearStat.school_year_id == year.id).delete(synchronize_session=False)
    db.flush()

    monthly_rows = (
        db.query(
            func.extract("year", models.VisitorDailyCount.visit_date).label("year"),
            func.extract("month", models.VisitorDailyCount.visit_date).label("month"),
            func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0).label("total_visitors"),
            func.count(models.VisitorDailyCount.id).label("open_days"),
        )
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .group_by("year", "month")
        .all()
    )
    for row in monthly_rows:
        db.add(models.VisitorMonthlyStat(
            school_year_id=year.id,
            year=int(row.year),
            month=int(row.month),
            total_visitors=int(row.total_visitors or 0),
            open_days=int(row.open_days or 0),
        ))

    period_count = 0
    periods = db.query(models.VisitorPeriod).filter(models.VisitorPeriod.school_year_id == year.id).all()
    for period in periods:
        if not period.start_date or not period.end_date:
            continue
        totals = (
            db.query(
                func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0),
                func.count(models.VisitorDailyCount.id),
            )
            .filter(
                models.VisitorDailyCount.school_year_id == year.id,
                models.VisitorDailyCount.visit_date >= period.start_date,
                models.VisitorDailyCount.visit_date <= period.end_date,
            )
            .one()
        )
        db.add(models.VisitorPeriodStat(
            school_year_id=year.id,
            period_id=period.id,
            total_visitors=int(totals[0] or 0),
            open_days=int(totals[1] or 0),
        ))
        period_count += 1

    year_totals = (
        db.query(
            func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0),
            func.count(models.VisitorDailyCount.id),
        )
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .one()
    )
    db.add(models.VisitorYearStat(
        school_year_id=year.id,
        total_visitors=int(year_totals[0] or 0),
        open_days=int(year_totals[1] or 0),
    ))
    db.flush()
    return {"monthly_stats": len(monthly_rows), "period_stats": period_count, "year_stats": 1}

def _rebuild_period_stats(db: Session, year: models.VisitorSchoolYear) -> None:
    db.query(models.VisitorPeriodStat).filter(models.VisitorPeriodStat.school_year_id == year.id).delete()
    periods = (
        db.query(models.VisitorPeriod)
        .filter(models.VisitorPeriod.school_year_id == year.id)
        .all()
    )
    for period in periods:
        if not period.start_date or not period.end_date:
            continue
        entries = (
            db.query(models.VisitorDailyCount)
            .filter(
                models.VisitorDailyCount.school_year_id == year.id,
                models.VisitorDailyCount.visit_date >= period.start_date,
                models.VisitorDailyCount.visit_date <= period.end_date,
            )
            .all()
        )
        db.add(
            models.VisitorPeriodStat(
                school_year_id=year.id,
                period_id=period.id,
                total_visitors=sum(entry.daily_visitors for entry in entries),
                open_days=len(entries),
            )
        )


def _period_details(periods: list[models.VisitorPeriod]) -> dict[str, str | None]:
    details: dict[str, str | None] = {}
    period_map = {period.period_type: period for period in periods}
    for period_type in PERIOD_ORDER:
        key = PERIOD_DETAIL_KEYS[period_type]
        period = period_map.get(period_type)
        details[f"{key}_start"] = period.start_date.isoformat() if period and period.start_date else None
        details[f"{key}_end"] = period.end_date.isoformat() if period and period.end_date else None
    return details


def _validate_period_ranges(
    year: models.VisitorSchoolYear,
    period_map: dict[models.VisitorPeriodType, schemas.VisitorPeriodUpsert],
) -> None:
    missing = [period_type.value for period_type in PERIOD_ORDER if period_type not in period_map]
    if missing:
        raise HTTPException(status_code=400, detail="모든 학기/방학 기간을 입력해야 합니다.")

    previous_end: date | None = None
    for period_type in PERIOD_ORDER:
        period = period_map[period_type]
        if not period.start_date or not period.end_date:
            raise HTTPException(status_code=400, detail="모든 학기/방학 기간의 시작일과 종료일을 입력해야 합니다.")
        if period.start_date > period.end_date:
            raise HTTPException(status_code=400, detail="기간 시작일은 종료일보다 늦을 수 없습니다.")
        if period.start_date < year.start_date or period.end_date > year.end_date:
            raise HTTPException(status_code=400, detail="학기/방학 기간은 학년도 기간 안에 있어야 합니다.")
        if previous_end and previous_end >= period.start_date:
            raise HTTPException(status_code=400, detail="학기/방학 기간은 서로 겹치지 않고 순서대로 입력해야 합니다.")
        previous_end = period.end_date


def _ensure_monthly_stats(db: Session, year: models.VisitorSchoolYear) -> list[models.VisitorMonthlyStat]:
    existing_stats = (
        db.query(models.VisitorMonthlyStat)
        .filter(models.VisitorMonthlyStat.school_year_id == year.id)
        .all()
    )
    existing_keys = {(stat.year, stat.month) for stat in existing_stats}
    aggregates = (
        db.query(
            func.extract("year", models.VisitorDailyCount.visit_date).label("year"),
            func.extract("month", models.VisitorDailyCount.visit_date).label("month"),
            func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0).label("total_visitors"),
            func.count(models.VisitorDailyCount.id).label("open_days"),
        )
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .group_by("year", "month")
        .all()
    )
    for row in aggregates:
        year_value = int(row.year)
        month_value = int(row.month)
        if (year_value, month_value) in existing_keys:
            continue
        db.add(
            models.VisitorMonthlyStat(
                school_year_id=year.id,
                year=year_value,
                month=month_value,
                total_visitors=int(row.total_visitors or 0),
                open_days=int(row.open_days or 0),
            )
        )
    return existing_stats


def _ensure_year_stat(
    db: Session,
    year: models.VisitorSchoolYear,
    monthly_stats: list[models.VisitorMonthlyStat],
) -> models.VisitorYearStat:
    year_stat = (
        db.query(models.VisitorYearStat)
        .filter(models.VisitorYearStat.school_year_id == year.id)
        .first()
    )
    if year_stat:
        return year_stat
    if monthly_stats:
        total_visitors = sum(stat.total_visitors for stat in monthly_stats)
        open_days = sum(stat.open_days for stat in monthly_stats)
    else:
        totals = (
            db.query(
                func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0),
                func.count(models.VisitorDailyCount.id),
            )
            .filter(models.VisitorDailyCount.school_year_id == year.id)
            .first()
        )
        total_visitors = int(totals[0] or 0)
        open_days = int(totals[1] or 0)
    year_stat = models.VisitorYearStat(
        school_year_id=year.id,
        total_visitors=total_visitors,
        open_days=open_days,
    )
    db.add(year_stat)
    return year_stat


def _ensure_period_stats(
    db: Session,
    year: models.VisitorSchoolYear,
    periods: list[models.VisitorPeriod],
) -> None:
    existing_stats = (
        db.query(models.VisitorPeriodStat)
        .filter(models.VisitorPeriodStat.school_year_id == year.id)
        .all()
    )
    existing_period_ids = {stat.period_id for stat in existing_stats}
    for period in periods:
        if period.id in existing_period_ids:
            continue
        if not period.start_date or not period.end_date:
            continue
        totals = (
            db.query(
                func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0),
                func.count(models.VisitorDailyCount.id),
            )
            .filter(
                models.VisitorDailyCount.school_year_id == year.id,
                models.VisitorDailyCount.visit_date >= period.start_date,
                models.VisitorDailyCount.visit_date <= period.end_date,
            )
            .first()
        )
        total_visitors = int(totals[0] or 0)
        open_days = int(totals[1] or 0)
        db.add(
            models.VisitorPeriodStat(
                school_year_id=year.id,
                period_id=period.id,
                total_visitors=total_visitors,
                open_days=open_days,
            )
        )


def _month_iter(start_date: date, end_date: date):
    current = date(start_date.year, start_date.month, 1)
    end_marker = date(end_date.year, end_date.month, 1)
    while current <= end_marker:
        yield current.year, current.month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def _build_summary(
    year: models.VisitorSchoolYear,
    periods: list[models.VisitorPeriod],
    monthly_stats: list[models.VisitorMonthlyStat],
    period_stats: list[models.VisitorPeriodStat],
    year_stat: models.VisitorYearStat | None,
) -> schemas.VisitorSummary:
    total_visitors = year_stat.total_visitors if year_stat else 0
    open_days = year_stat.open_days if year_stat else 0

    monthly_map = {(stat.year, stat.month): stat for stat in monthly_stats}
    monthly_out: list[schemas.VisitorMonthlyStat] = []
    for year_value, month_value in _month_iter(year.start_date, year.end_date):
        stat = monthly_map.get((year_value, month_value))
        label = f"{year_value}년 {month_value}월"
        monthly_out.append(
            schemas.VisitorMonthlyStat(
                year=year_value,
                month=month_value,
                label=label,
                open_days=stat.open_days if stat else 0,
                total_visitors=stat.total_visitors if stat else 0,
            )
        )

    period_stat_map = {stat.period_id: stat for stat in period_stats}
    period_out: list[schemas.VisitorPeriodStat] = []
    for period in periods:
        stat = period_stat_map.get(period.id)
        period_out.append(
            schemas.VisitorPeriodStat(
                period_type=period.period_type,
                name=period.name,
                start_date=period.start_date,
                end_date=period.end_date,
                open_days=stat.open_days if stat else 0,
                total_visitors=stat.total_visitors if stat else 0,
            )
        )

    return schemas.VisitorSummary(
        total_visitors=total_visitors,
        open_days=open_days,
        monthly=monthly_out,
        periods=period_out,
    )


@router.get("/years", response_model=list[schemas.VisitorYearOut])
def list_years(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return db.query(models.VisitorSchoolYear).order_by(models.VisitorSchoolYear.academic_year.desc()).all()


@router.post("/years", response_model=schemas.VisitorYearOut, status_code=status.HTTP_201_CREATED)
def create_year(payload: schemas.VisitorYearCreate, db: Session = Depends(get_db), current_user=Depends(require_role(models.UserRole.OPERATOR))):
    if db.query(models.VisitorSchoolYear).filter(models.VisitorSchoolYear.academic_year == payload.academic_year).first():
        raise HTTPException(status_code=400, detail="이미 등록된 학년도입니다.")
    start_date, end_date = _default_year_dates(payload.academic_year)
    if payload.start_date:
        start_date = payload.start_date
    if payload.end_date:
        end_date = payload.end_date
    default_periods = _default_period_ranges(payload.academic_year, start_date, end_date)
    period_overrides = {item.period_type: item for item in (payload.periods or [])}
    label = payload.label or f"{payload.academic_year}학년도 참고자료실 출입자 통계"
    year = models.VisitorSchoolYear(
        academic_year=payload.academic_year,
        label=label,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(year)
    db.flush()
    for period_type, default_name in PERIOD_DEFAULTS.items():
        override = period_overrides.get(period_type)
        default_start, default_end = default_periods[period_type]
        start_value = override.start_date if override and override.start_date else default_start
        end_value = override.end_date if override and override.end_date else default_end
        db.add(
            models.VisitorPeriod(
                school_year_id=year.id,
                period_type=period_type,
                name=default_name,
                start_date=start_value,
                end_date=end_value,
            )
    )
    db.add(models.VisitorRunningTotal(school_year_id=year.id))
    db.add(models.VisitorYearStat(school_year_id=year.id))
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_YEAR_CREATE",
        details={"year_id": str(year.id), "academic_year": year.academic_year},
    )
    db.commit()
    db.refresh(year)
    return year


@router.get("/years/{year_id}", response_model=schemas.VisitorYearDetail)
def get_year_detail(year_id, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    year = _get_year(db, year_id)
    periods = (
        db.query(models.VisitorPeriod)
        .filter(models.VisitorPeriod.school_year_id == year.id)
        .order_by(models.VisitorPeriod.period_type.asc())
        .all()
    )
    monthly_stats = _ensure_monthly_stats(db, year)
    year_stat = _ensure_year_stat(db, year, monthly_stats)
    _ensure_period_stats(db, year, periods)
    db.flush()
    period_stats = (
        db.query(models.VisitorPeriodStat)
        .filter(models.VisitorPeriodStat.school_year_id == year.id)
        .all()
    )
    summary = _build_summary(year, periods, monthly_stats, period_stats, year_stat)
    return schemas.VisitorYearDetail(
        year=schemas.VisitorYearOut.model_validate(year),
        periods=[schemas.VisitorPeriodOut.model_validate(period) for period in periods],
        entries=[],
        summary=summary,
    )


@router.get("/years/{year_id}/entries", response_model=list[schemas.VisitorEntryOut])
def list_entries(
    year_id,
    month: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    year = _get_year(db, year_id)
    query = db.query(models.VisitorDailyCount).filter(models.VisitorDailyCount.school_year_id == year.id)
    if month:
        try:
            year_value, month_value = month.split("-")
            year_int = int(year_value)
            month_int = int(month_value)
            if not (1 <= month_int <= 12):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="월 형식은 YYYY-MM 이어야 합니다.") from exc
        start_date = date(year_int, month_int, 1)
        end_day = calendar.monthrange(year_int, month_int)[1]
        end_date = date(year_int, month_int, end_day)
        query = query.filter(models.VisitorDailyCount.visit_date.between(start_date, end_date))
    creator = aliased(models.User)
    updater = aliased(models.User)
    entries = (
        query
        .outerjoin(creator, creator.id == models.VisitorDailyCount.created_by)
        .outerjoin(updater, updater.id == models.VisitorDailyCount.updated_by)
        .with_entities(
            models.VisitorDailyCount,
            creator.name.label("creator_name"),
            updater.name.label("updater_name"),
        )
        .order_by(models.VisitorDailyCount.visit_date.desc())
        .all()
    )
    entry_out = []
    for entry, creator_name, updater_name in entries:
        entry_out.append(_entry_out(entry, creator_name, updater_name))
    return entry_out


@router.put("/years/{year_id}", response_model=schemas.VisitorYearOut)
def update_year(year_id, payload: schemas.VisitorYearUpdate, db: Session = Depends(get_db), current_user=Depends(require_role(models.UserRole.OPERATOR))):
    year = _get_year(db, year_id)
    if payload.label is not None:
        year.label = payload.label
    if payload.start_date is not None:
        year.start_date = payload.start_date
    if payload.end_date is not None:
        year.end_date = payload.end_date
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_YEAR_UPDATE",
        details={"year_id": str(year.id), "academic_year": year.academic_year},
    )
    db.commit()
    db.refresh(year)
    return year


@router.delete("/years/{year_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_year(year_id, db: Session = Depends(get_db), current_user=Depends(require_role(models.UserRole.OPERATOR))):
    year = _get_year(db, year_id)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_YEAR_DELETE",
        details={"year_id": str(year.id), "academic_year": year.academic_year},
    )
    db.delete(year)
    db.commit()
    return None


@router.put("/years/{year_id}/periods", response_model=list[schemas.VisitorPeriodOut])
def upsert_periods(year_id, payload: list[schemas.VisitorPeriodUpsert], db: Session = Depends(get_db), current_user=Depends(require_role(models.UserRole.OPERATOR))):
    year = _get_year(db, year_id)
    if not payload:
        raise HTTPException(status_code=400, detail="수정할 학기/방학 기간을 입력해야 합니다.")

    period_map: dict[models.VisitorPeriodType, schemas.VisitorPeriodUpsert] = {}
    for item in payload:
        if item.period_type in period_map:
            raise HTTPException(status_code=400, detail="중복된 기간 유형이 포함되어 있습니다.")
        period_map[item.period_type] = item
    _validate_period_ranges(year, period_map)

    periods = (
        db.query(models.VisitorPeriod)
        .filter(models.VisitorPeriod.school_year_id == year.id)
        .all()
    )
    before = _period_details(periods)
    existing = {period.period_type: period for period in periods}
    updated_periods: list[models.VisitorPeriod] = []
    for period_type in PERIOD_ORDER:
        item = period_map[period_type]
        period = existing.get(period_type)
        if not period:
            period = models.VisitorPeriod(
                school_year_id=year.id,
                period_type=period_type,
                name=item.name or PERIOD_DEFAULTS[period_type],
            )
            db.add(period)
        period.name = item.name or PERIOD_DEFAULTS[period_type]
        period.start_date = item.start_date
        period.end_date = item.end_date
        updated_periods.append(period)

    db.flush()
    _rebuild_period_stats(db, year)
    after = _period_details(updated_periods)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_YEAR_UPDATE",
        details={
            "year_id": str(year.id),
            "academic_year": year.academic_year,
            "before": before,
            "after": after,
        },
    )
    db.commit()
    return (
        db.query(models.VisitorPeriod)
        .filter(models.VisitorPeriod.school_year_id == year.id)
        .order_by(models.VisitorPeriod.period_type.asc())
        .all()
    )


@router.post("/years/{year_id}/running-total/load", response_model=schemas.VisitorRunningTotalOut)
def load_running_total(year_id, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    year = _get_year(db, year_id)
    running = _get_running_total(db, year)
    db.commit()
    db.refresh(running)
    return schemas.VisitorRunningTotalOut(
        previous_total=running.previous_total,
        current_total=running.current_total,
        running_date=running.running_date,
    )


@router.post("/years/{year_id}/entries", response_model=schemas.VisitorEntryOut)
def upsert_entry(year_id, payload: schemas.VisitorEntryCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    year = _get_year(db, year_id)
    _ensure_within_year(year, payload.visit_date)
    today = _today_seoul()
    if payload.visit_date != today:
        raise HTTPException(status_code=403, detail="일일 입력은 오늘 날짜만 가능합니다.")

    _ensure_non_negative("전일 합", payload.previous_total, MAX_COUNTER_TOTAL)
    _ensure_non_negative("Count 1", payload.count1, MAX_COUNTER_VALUE)
    _ensure_non_negative("Count 2", payload.count2, MAX_COUNTER_VALUE)
    current_total = payload.count1 + payload.count2
    if current_total > MAX_COUNTER_TOTAL:
        raise HTTPException(status_code=400, detail=f"금일 합은 {MAX_COUNTER_TOTAL:,} 이하만 입력할 수 있습니다.")
    daily_visitors = current_total - payload.previous_total
    if daily_visitors < 0:
        raise HTTPException(status_code=400, detail="금일 출입자가 음수입니다. 전일 합과 Count 값을 확인하세요.")
    _validate_daily_visitors(daily_visitors)

    try:
        entry = (
            db.query(models.VisitorDailyCount)
            .filter(
                models.VisitorDailyCount.school_year_id == year.id,
                models.VisitorDailyCount.visit_date == payload.visit_date,
            )
            .first()
        )
        is_new_entry = entry is None
        if entry:
            action = "VISITOR_DAILY_UPDATE"
            is_operator = current_user.role in (models.UserRole.OPERATOR, models.UserRole.MASTER)
            if not is_operator and entry.created_by != current_user.id:
                raise HTTPException(status_code=403, detail="본인이 입력한 기록만 수정할 수 있습니다.")
            delta_visitors = daily_visitors - entry.daily_visitors
            entry.updated_by = current_user.id
        else:
            action = "VISITOR_DAILY_CREATE"
            delta_visitors = daily_visitors
            entry = models.VisitorDailyCount(
                school_year_id=year.id,
                visit_date=payload.visit_date,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.add(entry)

        entry.previous_total = payload.previous_total
        entry.count1 = payload.count1
        entry.count2 = payload.count2
        entry.current_total = current_total
        entry.daily_visitors = daily_visitors
        db.flush()

        running = _get_running_total(db, year)
        running.previous_total = current_total
        running.current_total = current_total
        running.running_date = today
        _apply_entry_delta(db, year, payload.visit_date, delta_visitors, 1 if is_new_entry else 0)
        record_log(
            db,
            actor_id=str(current_user.id),
            action=action,
            details={
                "year_id": str(year.id),
                "entry_id": str(entry.id) if entry.id else None,
                "visit_date": payload.visit_date.isoformat(),
                "previous_total": payload.previous_total,
                "count1": payload.count1,
                "count2": payload.count2,
                "current_total": current_total,
                "daily_visitors": daily_visitors,
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="같은 학년도와 날짜의 출입자 기록이 이미 저장되었습니다. 새로고침 후 다시 시도하세요.") from exc

    db.refresh(entry)
    creator_name = db.query(models.User.name).filter(models.User.id == entry.created_by).scalar() if entry.created_by else None
    updater_name = db.query(models.User.name).filter(models.User.id == entry.updated_by).scalar() if entry.updated_by else None
    return _entry_out(entry, creator_name, updater_name)

@router.delete("/years/{year_id}/entries", status_code=status.HTTP_204_NO_CONTENT)
def delete_entries(
    year_id,
    month: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    year = _get_year(db, year_id)
    query = db.query(models.VisitorDailyCount).filter(models.VisitorDailyCount.school_year_id == year.id)
    if month:
        try:
            year_value, month_value = month.split("-")
            year_int = int(year_value)
            month_int = int(month_value)
            if not (1 <= month_int <= 12):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="월 형식은 YYYY-MM 이어야 합니다.") from exc
        start_date = date(year_int, month_int, 1)
        end_day = calendar.monthrange(year_int, month_int)[1]
        end_date = date(year_int, month_int, end_day)
        query = query.filter(models.VisitorDailyCount.visit_date.between(start_date, end_date))
    entries = query.all()
    if entries:
        for entry in entries:
            _apply_entry_delta(db, year, entry.visit_date, -entry.daily_visitors, -1)
        record_log(
            db,
            actor_id=str(current_user.id),
            action="VISITOR_RESET",
            details={
                "year_id": str(year.id),
                "month": month,
                "deleted_entries": len(entries),
            },
        )
        for entry in entries:
            db.delete(entry)
    db.commit()
    return None


@router.post("/years/{year_id}/entries/bulk", response_model=list[schemas.VisitorEntryOut])
def bulk_upsert_entries(
    year_id,
    payload: schemas.VisitorBulkEntryRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    year = _get_year(db, year_id)
    if not payload.entries:
        raise HTTPException(status_code=400, detail="입력할 데이터가 없습니다.")

    seen_dates: set[date] = set()
    for item in payload.entries:
        _ensure_within_year(year, item.visit_date)
        if item.visit_date >= _today_seoul():
            raise HTTPException(status_code=400, detail="오늘 날짜는 일일 입력에서만 가능합니다.")
        _validate_daily_visitors(item.daily_visitors)
        if item.visit_date in seen_dates:
            raise HTTPException(status_code=400, detail="중복된 날짜가 포함되어 있습니다.")
        seen_dates.add(item.visit_date)

    dates = list(seen_dates)
    existing_entries = (
        db.query(models.VisitorDailyCount)
        .filter(
            models.VisitorDailyCount.school_year_id == year.id,
            models.VisitorDailyCount.visit_date.in_(dates),
        )
        .all()
    )
    existing_map = {entry.visit_date: entry for entry in existing_entries}
    updated_entries: list[models.VisitorDailyCount] = []

    try:
        for item in payload.entries:
            entry = existing_map.get(item.visit_date)
            if entry:
                if entry.daily_visitors == item.daily_visitors:
                    continue
                action = "VISITOR_DAILY_UPDATE"
                delta_visitors = item.daily_visitors - entry.daily_visitors
                entry.daily_visitors = item.daily_visitors
                if item.previous_total is not None and item.count1 is not None and item.count2 is not None:
                    entry.previous_total = item.previous_total
                    entry.count1 = item.count1
                    entry.count2 = item.count2
                    entry.current_total = item.count1 + item.count2
                else:
                    entry.previous_total = None
                    entry.count1 = None
                    entry.count2 = None
                    entry.current_total = None
                entry.updated_by = current_user.id
                _apply_entry_delta(db, year, item.visit_date, delta_visitors, 0)
            else:
                action = "VISITOR_DAILY_CREATE"
                entry = models.VisitorDailyCount(
                    school_year_id=year.id,
                    visit_date=item.visit_date,
                    daily_visitors=item.daily_visitors,
                    previous_total=item.previous_total if item.previous_total is not None and item.count1 is not None and item.count2 is not None else None,
                    count1=item.count1 if item.previous_total is not None and item.count1 is not None and item.count2 is not None else None,
                    count2=item.count2 if item.previous_total is not None and item.count1 is not None and item.count2 is not None else None,
                    current_total=(item.count1 + item.count2) if item.previous_total is not None and item.count1 is not None and item.count2 is not None else None,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                )
                db.add(entry)
                db.flush()
                _apply_entry_delta(db, year, item.visit_date, item.daily_visitors, 1)
            record_log(
                db,
                actor_id=str(current_user.id),
                action=action,
                details={
                    "year_id": str(year.id),
                    "entry_id": str(entry.id) if entry.id else None,
                    "visit_date": item.visit_date.isoformat(),
                    "daily_visitors": item.daily_visitors,
                },
            )
            updated_entries.append(entry)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="일괄 저장 중 오류가 발생했습니다.") from exc

    users = {u.id: u for u in db.query(models.User).all()}
    results: list[schemas.VisitorEntryOut] = []
    for entry in updated_entries:
        db.refresh(entry)
        results.append(_entry_out(
            entry,
            users.get(entry.created_by).name if entry.created_by in users else None,
            users.get(entry.updated_by).name if entry.updated_by in users else None,
        ))
    return results



@router.post("/years/{year_id}/repair-stats")
def repair_year_stats(year_id, db: Session = Depends(get_db), current_user=Depends(require_role(models.UserRole.OPERATOR))):
    year = _get_year(db, year_id)
    result = rebuild_visitor_stats(db, year)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_STATS_REPAIR",
        details={"year_id": str(year.id), **result},
    )
    db.commit()
    return result

@router.delete("/years/{year_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(year_id, entry_id, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    year = _get_year(db, year_id)
    entry = (
        db.query(models.VisitorDailyCount)
        .filter(models.VisitorDailyCount.school_year_id == year.id, models.VisitorDailyCount.id == entry_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="기록을 찾을 수 없습니다.")
    is_operator = current_user.role in (models.UserRole.OPERATOR, models.UserRole.MASTER)
    today = _today_seoul()
    if not is_operator:
        if entry.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="본인이 입력한 기록만 삭제할 수 있습니다.")
        if entry.visit_date != today:
            raise HTTPException(status_code=403, detail="오늘 날짜만 삭제할 수 있습니다.")
    _apply_entry_delta(db, year, entry.visit_date, -entry.daily_visitors, -1)
    if entry.visit_date == today:
        running = _get_running_total(db, year)
        if running.running_date == today:
            running.current_total = None
    record_log(
        db,
        actor_id=str(current_user.id),
        action="VISITOR_DAILY_DELETE",
        details={
            "year_id": str(year.id),
            "entry_id": str(entry.id),
            "visit_date": entry.visit_date.isoformat(),
            "daily_visitors": entry.daily_visitors,
        },
    )
    db.delete(entry)
    db.commit()
    return None
