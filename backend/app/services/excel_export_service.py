from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
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


def _get_visitor_year(db: Session, academic_year: int | None = None, year_id=None) -> models.VisitorSchoolYear:
    query = db.query(models.VisitorSchoolYear)
    if year_id is not None:
        year = query.filter(models.VisitorSchoolYear.id == year_id).first()
    elif academic_year is not None:
        year = query.filter(models.VisitorSchoolYear.academic_year == academic_year).first()
    else:
        year = None
    if not year:
        raise ValueError("visitor_year_not_found")
    return year


def build_visitors_excel(db: Session, academic_year: int | None = None, *, year_id=None) -> Path:
    year = _get_visitor_year(db, academic_year=academic_year, year_id=year_id)

    wb = Workbook()
    ws = wb.active
    ws.title = str(year.academic_year)

    # Legacy worksheet shape: A1:M36 contains the year calendar, O:R contains the
    # counter continuation helper used by the former Excel workflow.
    ws.merge_cells("A1:M1")
    ws["A1"] = f"{year.academic_year}학년도 참고열람실 출입자 통계"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A1"].alignment = Alignment(horizontal="center")

    months = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2]
    month_columns = {month: index + 2 for index, month in enumerate(months)}
    header_fill = PatternFill("solid", fgColor="E5E7EB")
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")

    for index, month in enumerate(months, start=2):
        cell = ws.cell(row=3, column=index, value=f"{month}월")
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border

    for day in range(1, 32):
        cell = ws.cell(row=day + 3, column=1, value=f"{day}일")
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border
        for column in range(2, 14):
            data_cell = ws.cell(row=day + 3, column=column)
            data_cell.border = border
            data_cell.alignment = center
            data_cell.number_format = "#,##0"

    entries = (
        db.query(models.VisitorDailyCount)
        .filter(models.VisitorDailyCount.school_year_id == year.id)
        .all()
    )
    for entry in entries:
        visit_date = entry.visit_date
        if visit_date.month not in month_columns:
            continue
        expected_year = year.academic_year if visit_date.month >= 3 else year.academic_year + 1
        if visit_date.year != expected_year or not (1 <= visit_date.day <= 31):
            continue
        ws.cell(row=visit_date.day + 3, column=month_columns[visit_date.month], value=entry.daily_visitors)

    for column in range(2, 14):
        total_cell = ws.cell(row=35, column=column, value=f"=SUM({get_column_letter(column)}4:{get_column_letter(column)}34)")
        open_cell = ws.cell(row=36, column=column, value=f"=COUNT({get_column_letter(column)}4:{get_column_letter(column)}34)")
        for cell in (total_cell, open_cell):
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.border = border
            cell.alignment = center
            cell.number_format = "#,##0"
    ws["A35"] = "합계"
    ws["A36"] = "개관일수"
    for cell_ref in ("A35", "A36"):
        ws[cell_ref].font = Font(bold=True)
        ws[cell_ref].fill = header_fill
        ws[cell_ref].border = border
        ws[cell_ref].alignment = center

    running = (
        db.query(models.VisitorRunningTotal)
        .filter(models.VisitorRunningTotal.school_year_id == year.id)
        .first()
    )
    next_previous_total = running.previous_total if running and running.previous_total is not None else None
    if next_previous_total is None:
        latest_with_current = (
            db.query(models.VisitorDailyCount)
            .filter(
                models.VisitorDailyCount.school_year_id == year.id,
                models.VisitorDailyCount.current_total.isnot(None),
            )
            .order_by(models.VisitorDailyCount.visit_date.desc())
            .first()
        )
        if latest_with_current:
            next_previous_total = latest_with_current.current_total

    helper_headers = {
        "O3": "전일 합",
        "O6": "Count1",
        "P6": "Count2",
        "Q6": "금일 합",
        "R6": "금일 출입자",
    }
    for cell_ref, value in helper_headers.items():
        cell = ws[cell_ref]
        cell.value = value
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border
    ws["O4"] = next_previous_total
    ws["Q7"] = "=O7+P7"
    ws["R7"] = '=IF(OR(Q7="",O4=""),"",Q7-O4)'
    for row in range(4, 8):
        for column in range(15, 19):
            cell = ws.cell(row=row, column=column)
            cell.border = border
            cell.alignment = center
            cell.number_format = "#,##0"

    widths = {
        "A": 10,
        **{get_column_letter(column): 11 for column in range(2, 14)},
        "N": 3,
        "O": 13,
        "P": 13,
        "Q": 13,
        "R": 15,
    }
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    for row in range(1, 37):
        ws.row_dimensions[row].height = 20

    ws.freeze_panes = "B4"
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
