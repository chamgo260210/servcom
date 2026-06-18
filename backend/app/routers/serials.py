# File: /backend/app/routers/serials.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_db
from ..core.audit import record_log
from ..core.roles import get_current_user, require_role

router = APIRouter(prefix="/serials", tags=["serials"])


def _get_publication(db: Session, publication_id) -> models.SerialPublication:
    publication = (
        db.query(models.SerialPublication)
        .filter(models.SerialPublication.id == publication_id)
        .first()
    )
    if not publication:
        raise HTTPException(status_code=404, detail="연속 간행물을 찾을 수 없습니다.")
    return publication


def _normalize_issn(issn: str | None) -> str | None:
    if issn is None:
        return None
    normalized = issn.strip()
    return normalized or None


def _ensure_unique_issn(
    db: Session,
    issn: str | None,
    publication_id=None,
) -> None:
    if not issn:
        return
    query = db.query(models.SerialPublication).filter(models.SerialPublication.issn == issn)
    if publication_id is not None:
        query = query.filter(models.SerialPublication.id != publication_id)
    if query.first():
        raise HTTPException(status_code=400, detail="이미 등록된 ISSN입니다.")


def _validate_shelf_position(
    db: Session,
    shelf_id,
    shelf_row: int | None,
    shelf_column: int | None,
    shelf_row_end: int | None,
    shelf_column_end: int | None,
) -> tuple[int | None, int | None]:
    location_values = [shelf_row, shelf_column, shelf_row_end, shelf_column_end]
    has_location = any(value is not None for value in location_values)
    if not shelf_id:
        if has_location:
            raise HTTPException(status_code=400, detail="서가 위치 시작/끝 값이 올바르지 않습니다.")
        return shelf_row_end, shelf_column_end

    shelf = (
        db.query(models.SerialShelf)
        .filter(models.SerialShelf.id == shelf_id)
        .first()
    )
    if not shelf or not shelf.shelf_type:
        raise HTTPException(status_code=400, detail="서가 위치 시작/끝 값이 올바르지 않습니다.")

    if shelf_row is None or shelf_column is None:
        raise HTTPException(status_code=400, detail="서가 위치 시작/끝 값이 올바르지 않습니다.")

    normalized_row_end = shelf_row if shelf_row_end is None else shelf_row_end
    normalized_column_end = shelf_column if shelf_column_end is None else shelf_column_end

    if (
        shelf_row < 1
        or shelf_column < 1
        or normalized_row_end < 1
        or normalized_column_end < 1
        or normalized_row_end < shelf_row
        or normalized_column_end < shelf_column
    ):
        raise HTTPException(status_code=400, detail="서가 위치 시작/끝 값이 올바르지 않습니다.")

    if (
        normalized_row_end > shelf.shelf_type.rows
        or normalized_column_end > shelf.shelf_type.columns
    ):
        raise HTTPException(status_code=400, detail="서가 위치가 서가 행/열 범위를 벗어났습니다.")

    return normalized_row_end, normalized_column_end


@router.get("", response_model=list[schemas.SerialPublicationOut])
def list_publications(
    q: str | None = None,
    issn: str | None = None,
    shelf_section: str | None = None,
    acquisition_type: models.SerialAcquisitionType | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(models.SerialPublication)
    if q:
        keyword = f"%{q.strip()}%"
        query = query.filter(models.SerialPublication.title.ilike(keyword))
    if issn:
        keyword = f"%{issn.strip()}%"
        query = query.filter(models.SerialPublication.issn.ilike(keyword))
    if shelf_section:
        keyword = f"%{shelf_section.strip()}%"
        query = query.filter(models.SerialPublication.shelf_section.ilike(keyword))
    if acquisition_type:
        query = query.filter(models.SerialPublication.acquisition_type == acquisition_type)
    return query.order_by(models.SerialPublication.title.asc()).all()


@router.get("/publications/{publication_id}", response_model=schemas.SerialPublicationOut)
def get_publication(
    publication_id,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _get_publication(db, publication_id)


@router.post("", response_model=schemas.SerialPublicationOut, status_code=status.HTTP_201_CREATED)
def create_publication(
    payload: schemas.SerialPublicationCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    issn = _normalize_issn(payload.issn)
    _ensure_unique_issn(db, issn)
    shelf_row_end, shelf_column_end = _validate_shelf_position(
        db,
        payload.shelf_id,
        payload.shelf_row,
        payload.shelf_column,
        payload.shelf_row_end,
        payload.shelf_column_end,
    )
    publication = models.SerialPublication(
        title=payload.title,
        issn=issn,
        acquisition_type=payload.acquisition_type,
        shelf_section=payload.shelf_section,
        shelf_id=payload.shelf_id,
        shelf_row=payload.shelf_row,
        shelf_column=payload.shelf_column,
        shelf_row_end=shelf_row_end,
        shelf_column_end=shelf_column_end,
        shelf_note=payload.shelf_note,
        remark=payload.remark,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(publication)
    db.flush()
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_PUBLICATION_CREATE",
        details={"publication_id": str(publication.id), "title": publication.title},
    )
    db.commit()
    db.refresh(publication)
    return publication


@router.put("/publications/{publication_id}", response_model=schemas.SerialPublicationOut)
def update_publication(
    publication_id,
    payload: schemas.SerialPublicationUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    publication = _get_publication(db, publication_id)
    issn = publication.issn
    if payload.issn is not None:
        issn = _normalize_issn(payload.issn)
    _ensure_unique_issn(db, issn, publication.id)

    next_shelf_id = publication.shelf_id if payload.shelf_id is None else payload.shelf_id
    next_shelf_row = publication.shelf_row if payload.shelf_row is None else payload.shelf_row
    next_shelf_column = publication.shelf_column if payload.shelf_column is None else payload.shelf_column
    next_shelf_row_end = publication.shelf_row_end if payload.shelf_row_end is None else payload.shelf_row_end
    next_shelf_column_end = (
        publication.shelf_column_end
        if payload.shelf_column_end is None
        else payload.shelf_column_end
    )
    next_shelf_row_end, next_shelf_column_end = _validate_shelf_position(
        db,
        next_shelf_id,
        next_shelf_row,
        next_shelf_column,
        next_shelf_row_end,
        next_shelf_column_end,
    )

    if payload.title is not None:
        publication.title = payload.title
    if payload.issn is not None:
        publication.issn = issn
    if payload.acquisition_type is not None:
        publication.acquisition_type = payload.acquisition_type
    if payload.shelf_section is not None:
        publication.shelf_section = payload.shelf_section
    if payload.shelf_id is not None:
        publication.shelf_id = payload.shelf_id
    if payload.shelf_row is not None:
        publication.shelf_row = payload.shelf_row
    if payload.shelf_column is not None:
        publication.shelf_column = payload.shelf_column
    if payload.shelf_row_end is not None:
        publication.shelf_row_end = next_shelf_row_end
    if payload.shelf_column_end is not None:
        publication.shelf_column_end = next_shelf_column_end
    if payload.shelf_row_end is None and next_shelf_row_end != publication.shelf_row_end:
        publication.shelf_row_end = next_shelf_row_end
    if payload.shelf_column_end is None and next_shelf_column_end != publication.shelf_column_end:
        publication.shelf_column_end = next_shelf_column_end
    if payload.shelf_note is not None:
        publication.shelf_note = payload.shelf_note
    if payload.remark is not None:
        publication.remark = payload.remark
    publication.updated_by = current_user.id
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_PUBLICATION_UPDATE",
        details={"publication_id": str(publication.id), "title": publication.title},
    )
    db.commit()
    db.refresh(publication)
    return publication


@router.delete("/publications/{publication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_publication(
    publication_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    publication = _get_publication(db, publication_id)
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_PUBLICATION_DELETE",
        details={"publication_id": str(publication.id), "title": publication.title},
    )
    db.delete(publication)
    db.commit()
    return None


@router.get("/layouts", response_model=list[schemas.SerialLayoutOut])
def list_layouts(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return db.query(models.SerialLayout).order_by(models.SerialLayout.created_at.asc()).all()
    except (ProgrammingError, OperationalError):
        return []


@router.post("/layouts", response_model=schemas.SerialLayoutOut, status_code=status.HTTP_201_CREATED)
def create_layout(
    payload: schemas.SerialLayoutCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    layout = models.SerialLayout(
        name=payload.name,
        width=payload.width,
        height=payload.height,
        note=payload.note,
        walls=payload.walls,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(layout)
    db.flush()
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_LAYOUT_UPDATE",
        details={"layout_id": str(layout.id), "name": layout.name, "operation": "CREATE"},
    )
    db.commit()
    db.refresh(layout)
    return layout


@router.put("/layouts/{layout_id}", response_model=schemas.SerialLayoutOut)
def update_layout(
    layout_id,
    payload: schemas.SerialLayoutUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    layout = db.query(models.SerialLayout).filter(models.SerialLayout.id == layout_id).first()
    if not layout:
        raise HTTPException(status_code=404, detail="배치도를 찾을 수 없습니다.")
    if payload.name is not None:
        layout.name = payload.name
    if payload.width is not None:
        layout.width = payload.width
    if payload.height is not None:
        layout.height = payload.height
    if payload.note is not None:
        layout.note = payload.note
    if payload.walls is not None:
        layout.walls = payload.walls
    layout.updated_by = current_user.id
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_LAYOUT_UPDATE",
        details={"layout_id": str(layout.id), "name": layout.name, "operation": "UPDATE"},
    )
    db.commit()
    db.refresh(layout)
    return layout


@router.delete("/layouts/{layout_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_layout(
    layout_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    layout = db.query(models.SerialLayout).filter(models.SerialLayout.id == layout_id).first()
    if not layout:
        raise HTTPException(status_code=404, detail="배치도를 찾을 수 없습니다.")
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_LAYOUT_UPDATE",
        details={"layout_id": str(layout.id), "name": layout.name, "operation": "DELETE"},
    )
    db.delete(layout)
    db.commit()
    return None


@router.get("/shelf-types", response_model=list[schemas.SerialShelfTypeOut])
def list_shelf_types(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return db.query(models.SerialShelfType).order_by(models.SerialShelfType.created_at.asc()).all()
    except (ProgrammingError, OperationalError):
        return []


@router.post("/shelf-types", response_model=schemas.SerialShelfTypeOut, status_code=status.HTTP_201_CREATED)
def create_shelf_type(
    payload: schemas.SerialShelfTypeCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf_type = models.SerialShelfType(
        name=payload.name,
        width=payload.width,
        height=payload.height,
        rows=payload.rows,
        columns=payload.columns,
        note=payload.note,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(shelf_type)
    db.flush()
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_TYPE_CREATE",
        details={"shelf_type_id": str(shelf_type.id), "name": shelf_type.name},
    )
    db.commit()
    db.refresh(shelf_type)
    return shelf_type


@router.put("/shelf-types/{shelf_type_id}", response_model=schemas.SerialShelfTypeOut)
def update_shelf_type(
    shelf_type_id,
    payload: schemas.SerialShelfTypeUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf_type = db.query(models.SerialShelfType).filter(models.SerialShelfType.id == shelf_type_id).first()
    if not shelf_type:
        raise HTTPException(status_code=404, detail="서가 타입을 찾을 수 없습니다.")
    if payload.name is not None:
        shelf_type.name = payload.name
    if payload.width is not None:
        shelf_type.width = payload.width
    if payload.height is not None:
        shelf_type.height = payload.height
    if payload.rows is not None:
        shelf_type.rows = payload.rows
    if payload.columns is not None:
        shelf_type.columns = payload.columns
    if payload.note is not None:
        shelf_type.note = payload.note
    shelf_type.updated_by = current_user.id
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_TYPE_UPDATE",
        details={"shelf_type_id": str(shelf_type.id), "name": shelf_type.name},
    )
    db.commit()
    db.refresh(shelf_type)
    return shelf_type


@router.delete("/shelf-types/{shelf_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shelf_type(
    shelf_type_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf_type = db.query(models.SerialShelfType).filter(models.SerialShelfType.id == shelf_type_id).first()
    if not shelf_type:
        raise HTTPException(status_code=404, detail="서가 타입을 찾을 수 없습니다.")
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_TYPE_DELETE",
        details={"shelf_type_id": str(shelf_type.id), "name": shelf_type.name},
    )
    db.delete(shelf_type)
    db.commit()
    return None


@router.get("/shelves", response_model=list[schemas.SerialShelfOut])
def list_shelves(
    layout_id: str | None = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        query = db.query(models.SerialShelf)
        if layout_id:
            query = query.filter(models.SerialShelf.layout_id == layout_id)
        return query.order_by(models.SerialShelf.created_at.asc()).all()
    except (ProgrammingError, OperationalError):
        return []


@router.post("/shelves", response_model=schemas.SerialShelfOut, status_code=status.HTTP_201_CREATED)
def create_shelf(
    payload: schemas.SerialShelfCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf = models.SerialShelf(
        layout_id=payload.layout_id,
        shelf_type_id=payload.shelf_type_id,
        code=payload.code,
        x=payload.x,
        y=payload.y,
        rotation=payload.rotation,
        note=payload.note,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(shelf)
    db.flush()
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_CREATE",
        details={"shelf_id": str(shelf.id), "layout_id": str(shelf.layout_id), "code": shelf.code},
    )
    db.commit()
    db.refresh(shelf)
    return shelf


@router.put("/shelves/{shelf_id}", response_model=schemas.SerialShelfOut)
def update_shelf(
    shelf_id,
    payload: schemas.SerialShelfUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf = db.query(models.SerialShelf).filter(models.SerialShelf.id == shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="서가를 찾을 수 없습니다.")
    if payload.layout_id is not None:
        shelf.layout_id = payload.layout_id
    if payload.shelf_type_id is not None:
        shelf.shelf_type_id = payload.shelf_type_id
    if payload.code is not None:
        shelf.code = payload.code
    if payload.x is not None:
        shelf.x = payload.x
    if payload.y is not None:
        shelf.y = payload.y
    if payload.rotation is not None:
        shelf.rotation = payload.rotation
    if payload.note is not None:
        shelf.note = payload.note
    shelf.updated_by = current_user.id
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_UPDATE",
        details={"shelf_id": str(shelf.id), "layout_id": str(shelf.layout_id), "code": shelf.code},
    )
    db.commit()
    db.refresh(shelf)
    return shelf


@router.delete("/shelves/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shelf(
    shelf_id,
    db: Session = Depends(get_db),
    current_user=Depends(require_role(models.UserRole.OPERATOR)),
):
    shelf = db.query(models.SerialShelf).filter(models.SerialShelf.id == shelf_id).first()
    if not shelf:
        raise HTTPException(status_code=404, detail="서가를 찾을 수 없습니다.")
    record_log(
        db,
        actor_id=str(current_user.id),
        action="SERIAL_SHELF_DELETE",
        details={"shelf_id": str(shelf.id), "layout_id": str(shelf.layout_id), "code": shelf.code},
    )
    db.delete(shelf)
    db.commit()
    return None
