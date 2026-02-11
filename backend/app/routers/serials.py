# File: /backend/app/routers/serials.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import get_db
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
    publication = models.SerialPublication(
        title=payload.title,
        issn=payload.issn,
        acquisition_type=payload.acquisition_type,
        shelf_section=payload.shelf_section,
        shelf_id=payload.shelf_id,
        shelf_row=payload.shelf_row,
        shelf_column=payload.shelf_column,
        shelf_row_end=payload.shelf_row_end,
        shelf_column_end=payload.shelf_column_end,
        shelf_note=payload.shelf_note,
        remark=payload.remark,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(publication)
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
    if payload.title is not None:
        publication.title = payload.title
    if payload.issn is not None:
        publication.issn = payload.issn
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
        publication.shelf_row_end = payload.shelf_row_end
    if payload.shelf_column_end is not None:
        publication.shelf_column_end = payload.shelf_column_end
    if payload.shelf_note is not None:
        publication.shelf_note = payload.shelf_note
    if payload.remark is not None:
        publication.remark = payload.remark
    publication.updated_by = current_user.id
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
    db.delete(shelf)
    db.commit()
    return None
