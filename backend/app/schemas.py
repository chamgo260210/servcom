# File: /backend/app/schemas.py
from datetime import datetime, date, time
import enum
from typing import Optional, List
from uuid import UUID  # ✅ UUID 타입 추가

from pydantic import BaseModel, Field, ConfigDict
from .models import (
    UserRole,
    RequestType,
    RequestStatus,
    NoticeType,
    NoticeChannel,
    NoticeScope,
    VisitorPeriodType,
    SerialAcquisitionType,
)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    # JWT "sub" 에는 user id(UUID)가 string으로 들어가지만,
    # Pydantic이 자동으로 UUID로 파싱해 줄 수 있음
    sub: UUID
    role: UserRole


class UserBase(BaseModel):
    name: str
    identifier: Optional[str] = None
    role: UserRole = UserRole.MEMBER
    active: bool = True


class UserCreate(UserBase):
    login_id: str
    password: str = Field(min_length=1)


class UserUpdate(BaseModel):
    name: Optional[str]
    identifier: Optional[str]
    role: Optional[UserRole]
    active: Optional[bool]


class UserOut(UserBase):
    # ORM 객체에서 바로 읽어오도록 설정 (Pydantic v2)
    model_config = ConfigDict(from_attributes=True)

    # 🔧 DB에서 UUID 컬럼이므로 UUID 타입으로 맞춰줌
    id: UUID
    auth_account: "AuthAccountOut | None" = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class AccountUpdate(BaseModel):
    current_password: str
    new_login_id: str | None = None
    new_password: str | None = Field(default=None, min_length=8)


class CredentialAdminUpdate(BaseModel):
    new_login_id: str | None = None
    new_password: str | None = Field(default=None, min_length=1)


class ShiftBase(BaseModel):
    name: str
    weekday: int
    start_time: time
    end_time: time
    location: Optional[str] = None


class ShiftCreate(ShiftBase):
    pass


class ShiftOut(ShiftBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID  # 🔧 UUID


class AssignmentCreate(BaseModel):
    # 🔧 FK 전부 UUID
    user_id: UUID
    shift_id: UUID
    valid_from: date
    valid_to: Optional[date] = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    shift_id: UUID
    valid_from: date
    valid_to: Optional[date]


class RequestCreate(BaseModel):
    type: RequestType
    target_date: date
    target_shift_id: UUID | None = None  # 🔧 UUID (단일 선택)
    target_shift_ids: list[UUID] | None = None  # 다중 슬롯 지원
    target_ranges: list["RequestRange"] | None = None  # 부분 시간 선택
    reason: str = Field(min_length=1)
    user_id: UUID | None = None


class RequestRange(BaseModel):
    shift_id: UUID
    start_hour: int | None = None
    end_hour: int | None = None


class RequestAction(BaseModel):
    decision: RequestStatus


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    type: RequestType
    target_date: date
    target_shift_id: UUID
    target_start_time: time | None = None
    target_end_time: time | None = None
    reason: Optional[str]
    status: RequestStatus
    operator_id: Optional[UUID]
    decided_at: Optional[datetime]
    cancelled_after_approval: bool
    cancel_reason: Optional[str]
    created_at: datetime


class RequestFeedEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: UUID
    action_type: str
    created_at: datetime
    user_id: UUID
    type: RequestType
    target_date: date
    target_shift_id: UUID
    target_start_time: time | None = None
    target_end_time: time | None = None
    reason: Optional[str]
    cancelled_after_approval: bool = False
    cancel_reason: Optional[str]


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: Optional[UUID]
    action_type: str
    target_user_id: Optional[UUID]
    request_id: Optional[UUID]
    details: Optional[dict]
    created_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: Optional[UUID]
    action_type: str
    target_user_id: Optional[UUID]
    request_id: Optional[UUID]
    details: Optional[dict]
    created_at: datetime


class HistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    action_type: str
    action_label: str
    actor_user_id: Optional[UUID]
    actor_name: Optional[str]
    target_user_id: Optional[UUID]
    target_name: Optional[str]
    request_id: Optional[UUID]
    details: Optional[dict]
    created_at: datetime


class AuthAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    login_id: str
    last_login_at: datetime | None = None


class ResetScope(str, enum.Enum):
    MEMBERS = "members"
    OPERATORS_AND_MEMBERS = "operators_members"
    ALL = "all"


class ResetRequest(BaseModel):
    scope: ResetScope


class NoticeBase(BaseModel):
    title: str
    body: str
    type: NoticeType
    channel: NoticeChannel
    scope: NoticeScope = NoticeScope.ALL
    target_roles: list[UserRole] | None = None
    target_user_ids: list[UUID] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    priority: int = 0
    is_active: bool = True


class NoticeCreate(NoticeBase):
    pass


class NoticeUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    type: NoticeType | None = None
    channel: NoticeChannel | None = None
    scope: NoticeScope | None = None
    target_roles: list[UserRole] | None = None
    target_user_ids: list[UUID] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    priority: int | None = None
    is_active: bool | None = None


class NoticeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body: str
    type: NoticeType
    channel: NoticeChannel
    scope: NoticeScope
    target_roles: list[UserRole] | None = None
    target_user_ids: list[UUID] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    priority: int
    is_active: bool
    created_by: UUID
    creator_role: UserRole | None = None
    created_at: datetime
    updated_at: datetime


class NoticeListItem(NoticeOut):
    read_at: datetime | None = None
    dismissed_at: datetime | None = None


class NoticeReadAction(BaseModel):
    channel: NoticeChannel


class ShiftSlot(BaseModel):
    weekday: int
    start_time: time
    end_time: time
    name: str | None = None
    location: str | None = None


class SlotAssign(BaseModel):
    user_id: UUID
    weekday: int
    start_hour: int
    end_hour: int | None = None
    valid_from: date
    valid_to: Optional[date] = None
    location: str | None = None


class SlotRange(BaseModel):
    weekday: int
    start_hour: int
    end_hour: int
    location: str | None = None


class SlotAssignBulk(BaseModel):
    user_id: UUID
    valid_from: date
    valid_to: Optional[date] = None
    slots: list[SlotRange]


class ScheduleEvent(BaseModel):
    user_id: UUID
    user_name: str
    role: UserRole
    date: date
    start_time: time
    end_time: time
    shift_id: UUID
    shift_name: str
    location: str | None = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    source: str = "BASE"  # BASE, EXTRA, ABSENCE


class VisitorYearBase(BaseModel):
    academic_year: int
    label: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class VisitorYearCreate(VisitorYearBase):
    periods: list["VisitorPeriodUpsert"] | None = None


class VisitorYearUpdate(BaseModel):
    label: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class VisitorYearOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    academic_year: int
    label: str
    start_date: date
    end_date: date
    created_at: datetime
    updated_at: datetime


class VisitorPeriodBase(BaseModel):
    period_type: VisitorPeriodType
    name: str
    start_date: date | None = None
    end_date: date | None = None


class VisitorPeriodUpsert(VisitorPeriodBase):
    pass


class VisitorPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_year_id: UUID
    period_type: VisitorPeriodType
    name: str
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime
    updated_at: datetime


class VisitorEntryCreate(BaseModel):
    visit_date: date
    daily_visitors: int
    previous_total: int | None = None


class VisitorBulkEntryItem(BaseModel):
    visit_date: date
    daily_visitors: int


class VisitorBulkEntryRequest(BaseModel):
    entries: list[VisitorBulkEntryItem]


class VisitorBulkEntryRequest(BaseModel):
    entries: list[VisitorBulkEntryItem]


class VisitorEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_year_id: UUID
    visit_date: date
    daily_visitors: int
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_by_name: str | None = None
    updated_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


class VisitorRunningTotalOut(BaseModel):
    previous_total: int | None = None
    current_total: int | None = None
    running_date: date | None = None


class VisitorMonthlyStat(BaseModel):
    year: int
    month: int
    label: str
    open_days: int
    total_visitors: int


class VisitorPeriodStat(BaseModel):
    period_type: VisitorPeriodType
    name: str
    start_date: date | None = None
    end_date: date | None = None
    open_days: int
    total_visitors: int


class VisitorSummary(BaseModel):
    total_visitors: int
    open_days: int
    monthly: list[VisitorMonthlyStat]
    periods: list[VisitorPeriodStat]


class VisitorYearDetail(BaseModel):
    year: VisitorYearOut
    periods: list[VisitorPeriodOut]
    entries: list[VisitorEntryOut]
    summary: VisitorSummary


class SerialPublicationBase(BaseModel):
    title: str
    issn: str | None = None
    acquisition_type: SerialAcquisitionType
    shelf_section: str
    shelf_id: UUID | None = None
    shelf_row: int | None = None
    shelf_column: int | None = None
    shelf_row_end: int | None = None
    shelf_column_end: int | None = None
    shelf_note: str | None = None
    remark: str | None = None


class SerialPublicationCreate(SerialPublicationBase):
    pass


class SerialPublicationUpdate(BaseModel):
    title: str | None = None
    issn: str | None = None
    acquisition_type: SerialAcquisitionType | None = None
    shelf_section: str | None = None
    shelf_id: UUID | None = None
    shelf_row: int | None = None
    shelf_column: int | None = None
    shelf_row_end: int | None = None
    shelf_column_end: int | None = None
    shelf_note: str | None = None
    remark: str | None = None


class SerialPublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    issn: str | None = None
    acquisition_type: SerialAcquisitionType
    shelf_section: str
    shelf_id: UUID | None = None
    shelf_row: int | None = None
    shelf_column: int | None = None
    shelf_row_end: int | None = None
    shelf_column_end: int | None = None
    shelf_note: str | None = None
    remark: str | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class SerialLayoutBase(BaseModel):
    name: str
    width: int = 800
    height: int = 500
    note: str | None = None
    walls: list[dict] | None = None


class SerialLayoutCreate(SerialLayoutBase):
    pass


class SerialLayoutUpdate(BaseModel):
    name: str | None = None
    width: int | None = None
    height: int | None = None
    note: str | None = None
    walls: list[dict] | None = None


class SerialLayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    width: int
    height: int
    note: str | None = None
    walls: list[dict] | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class SerialShelfTypeBase(BaseModel):
    name: str
    width: int = 80
    height: int = 40
    rows: int = 5
    columns: int = 5
    color: str | None = None
    note: str | None = None


class SerialShelfTypeCreate(SerialShelfTypeBase):
    pass


class SerialShelfTypeUpdate(BaseModel):
    name: str | None = None
    width: int | None = None
    height: int | None = None
    rows: int | None = None
    columns: int | None = None
    color: str | None = None
    note: str | None = None


class SerialShelfTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    width: int
    height: int
    rows: int
    columns: int
    color: str | None = None
    note: str | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class SerialShelfBase(BaseModel):
    layout_id: UUID
    shelf_type_id: UUID
    code: str
    x: int
    y: int
    rotation: int = 0
    note: str | None = None


class SerialShelfCreate(SerialShelfBase):
    pass


class SerialShelfUpdate(BaseModel):
    layout_id: UUID | None = None
    shelf_type_id: UUID | None = None
    code: str | None = None
    x: int | None = None
    y: int | None = None
    rotation: int | None = None
    note: str | None = None


class SerialShelfOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    layout_id: UUID
    shelf_type_id: UUID
    code: str
    x: int
    y: int
    rotation: int
    note: str | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


# Forward references
UserOut.model_rebuild()
RequestCreate.model_rebuild()
VisitorYearCreate.model_rebuild()
