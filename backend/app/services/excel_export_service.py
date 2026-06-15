from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models


def _style_header(ws) -> None:
    fill = PatternFill("solid", fgColor="E5E7EB")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = fill


def _append_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    _style_header(ws)
    for column_cells in ws.columns:
        max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 10), 40)


def _save_workbook(wb: Workbook) -> Path:
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp.close()
    path = Path(tmp.name)
    wb.save(path)
    return path


def build_visitors_excel(db: Session, academic_year: int) -> Path:
    year = (
        db.query(models.VisitorSchoolYear)
        .filter(models.VisitorSchoolYear.academic_year == academic_year)
        .first()
    )
    if not year:
        raise ValueError("visitor_year_not_found")

    wb = Workbook()
    wb.remove(wb.active)

    year_stat = (
        db.query(models.VisitorYearStat)
        .filter(models.VisitorYearStat.school_year_id == year.id)
        .first()
    )
    entry_totals = (
        db.query(
            func.coalesce(func.sum(models.VisitorDailyCount.daily_visitors), 0),
            func.count(models.VisitorDailyCount.id),
        )
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .first()
    )
    total_visitors = year_stat.total_visitors if year_stat else int(entry_totals[0] or 0)
    open_days = year_stat.open_days if year_stat else int(entry_totals[1] or 0)

    _append_sheet(
        wb,
        "요약",
        ["항목", "값"],
        [
            ["학년도", year.academic_year],
            ["라벨", year.label],
            ["시작일", year.start_date.isoformat()],
            ["종료일", year.end_date.isoformat()],
            ["총 방문자", total_visitors],
            ["개방일수", open_days],
        ],
    )

    entries = (
        db.query(models.VisitorDailyCount)
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .order_by(models.VisitorDailyCount.visit_date.asc())
        .all()
    )
    _append_sheet(
        wb,
        "일별",
        ["일자", "방문자 수", "생성자", "수정자", "생성일", "수정일"],
        [
            [
                entry.visit_date.isoformat(),
                entry.daily_visitors,
                str(entry.created_by) if entry.created_by else "",
                str(entry.updated_by) if entry.updated_by else "",
                entry.created_at.isoformat() if entry.created_at else "",
                entry.updated_at.isoformat() if entry.updated_at else "",
            ]
            for entry in entries
        ],
    )

    monthly = (
        db.query(models.VisitorMonthlyStat)
        .filter(models.VisitorMonthlyStat.school_year_id == year.id)
        .order_by(models.VisitorMonthlyStat.year.asc(), models.VisitorMonthlyStat.month.asc())
        .all()
    )
    _append_sheet(
        wb,
        "월별",
        ["연도", "월", "총 방문자", "개방일수"],
        [[row.year, row.month, row.total_visitors, row.open_days] for row in monthly],
    )

    period_rows = (
        db.query(models.VisitorPeriod, models.VisitorPeriodStat)
        .outerjoin(
            models.VisitorPeriodStat,
            models.VisitorPeriodStat.period_id == models.VisitorPeriod.id,
        )
        .filter(models.VisitorPeriod.school_year_id == year.id)
        .order_by(models.VisitorPeriod.period_type.asc())
        .all()
    )
    _append_sheet(
        wb,
        "기간별",
        ["기간 유형", "이름", "시작일", "종료일", "총 방문자", "개방일수"],
        [
            [
                period.period_type.value,
                period.name,
                period.start_date.isoformat() if period.start_date else "",
                period.end_date.isoformat() if period.end_date else "",
                stat.total_visitors if stat else 0,
                stat.open_days if stat else 0,
            ]
            for period, stat in period_rows
        ],
    )

    _append_sheet(
        wb,
        "연간",
        ["학년도", "총 방문자", "개방일수"],
        [[year.academic_year, total_visitors, open_days]],
    )
    return _save_workbook(wb)


def build_serials_excel(db: Session) -> Path:
    wb = Workbook()
    wb.remove(wb.active)

    publications = db.query(models.SerialPublication).order_by(models.SerialPublication.title.asc()).all()
    _append_sheet(
        wb,
        "간행물 목록",
        ["제목", "ISSN", "수집 유형", "서가 구역", "서가 ID", "행", "열", "행 끝", "열 끝", "서가 메모", "비고"],
        [
            [
                item.title,
                item.issn or "",
                item.acquisition_type.value,
                item.shelf_section,
                str(item.shelf_id) if item.shelf_id else "",
                item.shelf_row,
                item.shelf_column,
                item.shelf_row_end,
                item.shelf_column_end,
                item.shelf_note or "",
                item.remark or "",
            ]
            for item in publications
        ],
    )

    shelves = db.query(models.SerialShelf).order_by(models.SerialShelf.code.asc()).all()
    _append_sheet(
        wb,
        "서가 목록",
        ["코드", "배치도 ID", "서가 타입 ID", "X", "Y", "회전", "메모"],
        [[item.code, str(item.layout_id), str(item.shelf_type_id), item.x, item.y, item.rotation, item.note or ""] for item in shelves],
    )

    layouts = db.query(models.SerialLayout).order_by(models.SerialLayout.name.asc()).all()
    _append_sheet(
        wb,
        "배치도 목록",
        ["이름", "너비", "높이", "메모", "벽 데이터"],
        [[item.name, item.width, item.height, item.note or "", str(item.walls or "")] for item in layouts],
    )

    shelf_types = db.query(models.SerialShelfType).order_by(models.SerialShelfType.name.asc()).all()
    _append_sheet(
        wb,
        "서가 타입",
        ["이름", "너비", "높이", "행", "열", "색상", "메모"],
        [[item.name, item.width, item.height, item.rows, item.columns, item.color or "", item.note or ""] for item in shelf_types],
    )
    return _save_workbook(wb)
