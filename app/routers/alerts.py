import csv
import io
import asyncio
from datetime import date, datetime, time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.services.websocket_manager import websocket_manager, safe_notify_dashboard

from app.constants import ASSET_TYPE_DISPLAY_GROUPS, ASSET_TYPE_MAP, PARAMETER_TYPE_MAP
from app.database import get_db
from app.models.models import AlertEvent, Asset, AssetParameter, Division, Station, Zone, AssetTypeMaster, AlertCauseMaster, AssetInventory, MaintenanceMode, Role, SlaveCard, Gateway, User
from app.auth_utils import get_current_user
from app.models.schemas import (
    AlertEventCreate,
    AlertEventResponse,
    AlertEventsResponse,
    AlertEventUpdate,
    AlertFeedbackUpdate,
    AlertFilterOption,
    AlertFiltersResponse,
    AlertHistoryResponse,
    AlertHistoryRow,
    AlertLiveCard,
    AlertLiveResponse,
    AlertLiveSummary,
    AlertRectificationUpdate,
    AlertRemarkUpdate,
    AlertSummaryResponse,
    AlertSummaryRow,
    AssetTypeGroupOption,
    AssetTypeOption,
    ChannelFilterOption,
    DropdownOption,
    SlaveCardFilterOption,
    StandardResponse
)

router = APIRouter(prefix="/alerts", tags=["Alerts"])

ALERT_TYPE_OPTIONS = ["ALL", "PREDICTIVE", "FAILURE"]
FEEDBACK_OPTIONS = ["ALL", "T", "PT", "F", "M"]
CAUSE_OPTIONS = ["ALL", "PT-OBS", "TC-SHUNT", "BAT-LOW", "COMM-FAIL", "TEMP-HIGH", "MOTOR-OC"]
ALERT_TYPE_ALIASES = {
    "PREDICTIVE": "Predictive",
    "FAILURE": "Failure",
    "FAILUR": "Failure",
}


# ---------------------------------------------------------------------------
# CSV safety helpers
# ---------------------------------------------------------------------------
_FORMULA_CHARS = frozenset("=+-@\t\r")


def _csv_safe(value: Optional[str]) -> str:
    """Prefix formula-trigger characters so Excel/Sheets won't evaluate them."""
    if not value:
        return value or ""
    if value[0] in _FORMULA_CHARS:
        return "'" + value
    return value


# ---------------------------------------------------------------------------
# LIKE wildcard escaping helper
# ---------------------------------------------------------------------------
def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards (%, _) in user-supplied search strings."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Shared string utilities
# ---------------------------------------------------------------------------
def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value or value.upper() == "ALL":
        return None
    return value


def _normalize_alert_type(value: Optional[str]) -> Optional[str]:
    value = _blank_to_none(value)
    if not value:
        return None
    return ALERT_TYPE_ALIASES.get(value.upper(), value)


def _combine_datetime(day: Optional[date], clock: Optional[time], is_end: bool) -> Optional[datetime]:
    if day is None and clock is None:
        return None
    if day is None:
        day = date.today()
    if clock is None:
        clock = time.max if is_end else time.min
    return datetime.combine(day, clock)


def _asset_type_name(asset_type_hex: str) -> str:
    asset_info = ASSET_TYPE_MAP.get(asset_type_hex)
    return asset_info[1] if asset_info else asset_type_hex


def _asset_group_hexes(asset_type: Optional[str]) -> Optional[List[str]]:
    asset_type = _blank_to_none(asset_type)
    if not asset_type:
        return None
    if asset_type.upper() in ASSET_TYPE_MAP:
        return [asset_type.upper()]
    return ASSET_TYPE_DISPLAY_GROUPS.get(asset_type)


def _resolve_asset_types_to_hex(db: Session, asset_type: Optional[str]) -> Optional[str]:
    asset_type = _blank_to_none(asset_type)
    if not asset_type:
        return None

    parts = [p.strip() for p in asset_type.split(",") if p.strip()]
    
    # Prioritize resolving parts as hex codes or display group names.
    group_map = {k.lower(): v for k, v in ASSET_TYPE_DISPLAY_GROUPS.items()}
    hex_list = []
    has_resolved = False
    for part in parts:
        part_upper = part.upper()
        part_lower = part.lower()
        if part_upper in ASSET_TYPE_MAP:
            hex_list.append(part_upper)
            has_resolved = True
        elif part_lower in group_map:
            hex_list.extend(group_map[part_lower])
            has_resolved = True
            
    if has_resolved:
        return ",".join(hex_list) if hex_list else "IMPOSSIBLE_HEX"

    # Fallback to database IDs if not matched by hex/group name and all parts are digits
    if all(p.isdigit() for p in parts):
        ids = [int(p) for p in parts]
        db_types = db.query(AssetTypeMaster).filter(AssetTypeMaster.id.in_(ids)).all()
        hexes = [t.asset_type_id for t in db_types if t.asset_type_id]
        if not hexes:
            return "IMPOSSIBLE_HEX"
        return ",".join(hexes)

    return None


def _page_meta(total_rows: int, page: int, page_size: int) -> tuple[int, int]:
    total_pages = (total_rows + page_size - 1) // page_size if total_rows else 0
    offset = (page - 1) * page_size
    return total_pages, offset


def _validate_event_payload(payload: AlertEventCreate | AlertEventUpdate, db: Session) -> None:
    if getattr(payload, "station_id", None) is not None:
        station = db.query(Station).filter(Station.id == payload.station_id).first()
        if not station:
            raise HTTPException(status_code=404, detail=f"Station {payload.station_id} not found")

    asset_type_hex = getattr(payload, "asset_type_hex", None)
    if asset_type_hex and asset_type_hex.upper() not in ASSET_TYPE_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown asset_type_hex '{asset_type_hex}'. See GET /assets/types.",
        )

    feedback = getattr(payload, "feedback", None)
    if feedback and feedback.upper() not in {"T", "PT", "F", "M"}:
        raise HTTPException(status_code=400, detail="feedback must be one of T, PT, F, M")


def _base_summary_query(
    db: Session,
    zone_id: Optional[int],
    division_id: Optional[int],
    station_id: Optional[int],
    zone: Optional[str],
    division: Optional[str],
    station: Optional[str],
    alert_type: Optional[str],
    asset_type_hex: Optional[str],
    asset_no: Optional[str],
    cause: Optional[str],
    from_date: Optional[date],
    from_time: Optional[time],
    to_date: Optional[date],
    to_time: Optional[time],
):
    q = (
        db.query(
            Zone.id.label("zone_id"),
            Zone.zone_code.label("zone"),
            Division.id.label("division_id"),
            Division.division_code.label("division"),
            Station.id.label("station_id"),
            Station.station_code.label("station"),
            AlertEvent.alert_type.label("alert_type"),
            AlertEvent.asset_type_hex.label("asset_type_hex"),
            AlertEvent.asset_no.label("asset_no"),
            AlertEvent.cause.label("cause"),
            func.count(AlertEvent.id).label("total"),
            func.sum(case((func.upper(AlertEvent.feedback) == "T", 1), else_=0)).label("true_count"),
            func.sum(case((func.upper(AlertEvent.feedback) == "PT", 1), else_=0)).label("partial_count"),
        )
        .join(Station, Station.id == AlertEvent.station_id)
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
    )

    zone = _blank_to_none(zone)
    division = _blank_to_none(division)
    station = _blank_to_none(station)
    alert_type = _normalize_alert_type(alert_type)
    asset_type_hex = _blank_to_none(asset_type_hex)
    asset_no = _blank_to_none(asset_no)
    cause = _blank_to_none(cause)

    if zone_id is not None:
        q = q.filter(Zone.id == zone_id)
    if division_id is not None:
        q = q.filter(Division.id == division_id)
    if station_id is not None:
        q = q.filter(Station.id == station_id)
    if zone:
        q = q.filter(func.upper(Zone.zone_code) == zone.upper())
    if division:
        q = q.filter(func.upper(Division.division_code) == division.upper())
    if station:
        q = q.filter(func.upper(Station.station_code) == station.upper())
    if alert_type:
        q = q.filter(func.lower(AlertEvent.alert_type) == alert_type.lower())

    if asset_type_hex:
        asset_hexes = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]
        if asset_hexes:
            q = q.filter(AlertEvent.asset_type_hex.in_(asset_hexes))

    if asset_no:
        q = q.filter(func.lower(AlertEvent.asset_no).like(f"%{_escape_like(asset_no.lower())}%", escape="\\"))
    if cause:
        q = q.filter(func.lower(AlertEvent.cause) == cause.lower())

    start_dt = _combine_datetime(from_date, from_time, is_end=False)
    end_dt = _combine_datetime(to_date, to_time, is_end=True)
    if start_dt:
        q = q.filter(AlertEvent.alert_time >= start_dt)
    if end_dt:
        q = q.filter(AlertEvent.alert_time <= end_dt)

    return (
        q.group_by(
            Zone.id,
            Zone.zone_code,
            Division.id,
            Division.division_code,
            Station.id,
            Station.station_code,
            AlertEvent.alert_type,
            AlertEvent.asset_type_hex,
            AlertEvent.asset_no,
            AlertEvent.cause,
        )
        .order_by(
            Zone.zone_code,
            Division.division_code,
            Station.station_code,
            AlertEvent.alert_type,
            AlertEvent.asset_type_hex,
            AlertEvent.asset_no,
            AlertEvent.cause,
        )
    )


def _summary_rows(raw_rows, start: int = 1) -> List[AlertSummaryRow]:
    rows: List[AlertSummaryRow] = []
    for idx, row in enumerate(raw_rows, start=start):
        total = int(row.total or 0)
        true_count = int(row.true_count or 0)
        partial_count = int(row.partial_count or 0)
        percentage = round(((true_count + partial_count) / total) * 100, 1) if total else 0.0
        rows.append(AlertSummaryRow(
            sr=idx,
            zone_id=row.zone_id,
            zone=row.zone,
            division_id=row.division_id,
            division=row.division,
            station_id=row.station_id,
            station=row.station,
            alert_type=row.alert_type,
            asset_type_hex=row.asset_type_hex,
            asset_type=_asset_type_name(row.asset_type_hex),
            asset_no=row.asset_no,
            cause=row.cause,
            total=total,
            true=true_count,
            partially_true=partial_count,
            percentage=percentage,
        ))
    return rows


def _base_history_query(
    db: Session,
    zone_id: Optional[int],
    division_id: Optional[int],
    station_id: Optional[int],
    zone: Optional[str],
    division: Optional[str],
    station: Optional[str],
    alert_type: Optional[str],
    asset_type_hex: Optional[str],
    asset_no: Optional[str],
    cause: Optional[str],
    feedback: Optional[str],
    alert_status: Optional[str],
    from_date: Optional[date],
    from_time: Optional[time],
    to_date: Optional[date],
    to_time: Optional[time],
):
    q = (
        db.query(
            AlertEvent.id.label("id"),
            Zone.id.label("zone_id"),
            Zone.zone_code.label("zone"),
            Division.id.label("division_id"),
            Division.division_code.label("division"),
            Station.id.label("station_id"),
            Station.station_code.label("station"),
            AlertEvent.alert_type.label("alert_type"),
            AlertEvent.asset_type_hex.label("asset_type_hex"),
            AlertEvent.asset_no.label("asset_no"),
            AlertEvent.alert_status.label("alert_status"),
            AlertEvent.cause.label("cause"),
            AlertEvent.feedback.label("feedback"),
            AlertEvent.alert_time.label("alert_time"),
            AlertEvent.rectification_time.label("rectification_time"),
            AlertEvent.feedback_time.label("feedback_time"),
            AlertEvent.maintainer_name.label("maintainer_name"),
            AlertEvent.designation.label("designation"),
            AlertEvent.mobile.label("mobile"),
            AlertEvent.remark.label("remark"),
        )
        .join(Station, Station.id == AlertEvent.station_id)
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
    )

    zone = _blank_to_none(zone)
    division = _blank_to_none(division)
    station = _blank_to_none(station)
    alert_type = _normalize_alert_type(alert_type)
    asset_type_hex = _blank_to_none(asset_type_hex)
    asset_no = _blank_to_none(asset_no)
    cause = _blank_to_none(cause)
    feedback = _blank_to_none(feedback)
    alert_status = _blank_to_none(alert_status)

    if zone_id is not None:
        q = q.filter(Zone.id == zone_id)
    if division_id is not None:
        q = q.filter(Division.id == division_id)
    if station_id is not None:
        q = q.filter(Station.id == station_id)
    if zone:
        q = q.filter(func.upper(Zone.zone_code) == zone.upper())
    if division:
        q = q.filter(func.upper(Division.division_code) == division.upper())
    if station:
        q = q.filter(func.upper(Station.station_code) == station.upper())
    if alert_type:
        q = q.filter(func.lower(AlertEvent.alert_type) == alert_type.lower())

    if asset_type_hex:
        asset_hexes = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]
        if asset_hexes:
            q = q.filter(AlertEvent.asset_type_hex.in_(asset_hexes))

    if asset_no:
        q = q.filter(func.lower(AlertEvent.asset_no).like(f"%{_escape_like(asset_no.lower())}%", escape="\\"))
    if cause:
        q = q.filter(func.lower(AlertEvent.cause) == cause.lower())
    if feedback:
        q = q.filter(func.upper(AlertEvent.feedback) == feedback.upper())
    if alert_status:
        q = q.filter(func.lower(AlertEvent.alert_status) == alert_status.lower())

    start_dt = _combine_datetime(from_date, from_time, is_end=False)
    end_dt = _combine_datetime(to_date, to_time, is_end=True)
    if start_dt:
        q = q.filter(AlertEvent.alert_time >= start_dt)
    if end_dt:
        q = q.filter(AlertEvent.alert_time <= end_dt)

    return q.order_by(AlertEvent.alert_time.desc(), AlertEvent.id.desc())


def _history_rows(raw_rows, start: int = 1) -> List[AlertHistoryRow]:
    rows: List[AlertHistoryRow] = []
    for idx, row in enumerate(raw_rows, start=start):
        duration_min = None
        if row.rectification_time and row.alert_time:
            duration_min = round((row.rectification_time - row.alert_time).total_seconds() / 60, 2)

        rows.append(AlertHistoryRow(
            sr=idx,
            id=row.id,
            zone_id=row.zone_id,
            zone=row.zone,
            division_id=row.division_id,
            division=row.division,
            station_id=row.station_id,
            station=row.station,
            alert_type=row.alert_type,
            asset_type_hex=row.asset_type_hex,
            asset_type=_asset_type_name(row.asset_type_hex),
            asset_no=row.asset_no,
            alert_status=row.alert_status,
            cause=row.cause,
            feedback=row.feedback,
            incidence_date_time=row.alert_time.isoformat(),
            rectification_date_time=row.rectification_time.isoformat() if row.rectification_time else None,
            duration_min=duration_min,
            feedback_date_time=row.feedback_time.isoformat() if row.feedback_time else None,
            maintainer_name=row.maintainer_name,
            designation=row.designation,
            mobile=row.mobile,
            remarks=row.remark,
        ))
    return rows


def _base_live_query(
    db: Session,
    zone_id: Optional[int],
    division_id: Optional[int],
    station_id: Optional[int],
    zone: Optional[str],
    division: Optional[str],
    station: Optional[str],
    alert_type: Optional[str],
    asset_type_hex: Optional[str],
    asset_no: Optional[str] = None,
):
    q = (
        db.query(
            AlertEvent.id.label("id"),
            Zone.id.label("zone_id"),
            Zone.zone_code.label("zone"),
            Division.id.label("division_id"),
            Division.division_code.label("division"),
            Station.id.label("station_id"),
            Station.station_code.label("station"),
            AlertEvent.alert_type.label("alert_type"),
            AlertEvent.asset_type_hex.label("asset_type_hex"),
            AlertEvent.asset_no.label("asset_no"),
            AlertEvent.alert_status.label("alert_status"),
            AlertEvent.cause.label("cause"),
            AlertEvent.feedback.label("feedback"),
            AlertEvent.acknowledged.label("acknowledged"),
            AlertEvent.alert_time.label("alert_time"),
            AlertEvent.remark.label("remark"),
        )
        .join(Station, Station.id == AlertEvent.station_id)
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
        .filter(AlertEvent.rectification_time.is_(None))
        .filter(func.lower(AlertEvent.alert_status) != "cleared")
    )

    zone = _blank_to_none(zone)
    division = _blank_to_none(division)
    station = _blank_to_none(station)
    alert_type = _normalize_alert_type(alert_type)
    asset_type_hex = _blank_to_none(asset_type_hex)
    asset_no = _blank_to_none(asset_no)

    if zone_id is not None:
        q = q.filter(Zone.id == zone_id)
    if division_id is not None:
        q = q.filter(Division.id == division_id)
    if station_id is not None:
        q = q.filter(Station.id == station_id)
    if zone:
        q = q.filter(func.upper(Zone.zone_code) == zone.upper())
    if division:
        q = q.filter(func.upper(Division.division_code) == division.upper())
    if station:
        q = q.filter(func.upper(Station.station_code) == station.upper())
    if alert_type:
        q = q.filter(func.lower(AlertEvent.alert_type) == alert_type.lower())
    if asset_no:
        q = q.filter(func.upper(AlertEvent.asset_no) == asset_no.upper().strip())

    if asset_type_hex:
        asset_hexes = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]
        if asset_hexes:
            q = q.filter(AlertEvent.asset_type_hex.in_(asset_hexes))

    return q.order_by(AlertEvent.acknowledged.asc(), AlertEvent.alert_time.desc(), AlertEvent.id.desc())


def _live_cards(raw_rows) -> List[AlertLiveCard]:
    cards: List[AlertLiveCard] = []
    for row in raw_rows:
        asset_type = _asset_type_name(row.asset_type_hex)
        cards.append(AlertLiveCard(
            id=row.id,
            zone_id=row.zone_id,
            zone=row.zone,
            division_id=row.division_id,
            division=row.division,
            station_id=row.station_id,
            station=row.station,
            title=f"{row.station} {row.asset_no}",
            alert_type=row.alert_type,
            asset_type_hex=row.asset_type_hex,
            asset_type=asset_type,
            asset_no=row.asset_no,
            alert_status=row.alert_status,
            cause=row.cause,
            feedback=row.feedback,
            acknowledged=bool(row.acknowledged),
            incidence_date_time=row.alert_time.isoformat(),
            remarks=row.remark,
        ))
    return cards


@router.get("/types", response_model=StandardResponse[List[AlertFilterOption]])
def list_alert_types():
    """
    Return a list of alert types for dropdown filters.
    """
    return {
        "status": True,
        "message": "Success",
        "data": [
            AlertFilterOption(id=1, label="All", value="ALL"),
            AlertFilterOption(id=2, label="Predictive", value="Predictive"),
            AlertFilterOption(id=3, label="Failure", value="Failure"),
        ]
    }


@router.get("/asset-numbers", response_model=StandardResponse[List[AlertFilterOption]])
def list_alert_asset_numbers(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    asset_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Return a list of unique asset numbers for dropdown filters.

    Primary source: Asset (all registered physical assets).
    Fallback: AlertEvent.asset_no for any unregistered asset numbers
    that appear in historical alerts.
    """
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    hex_list: Optional[List[str]] = None
    if asset_type_hex:
        hex_list = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]

    # ── 1. Query Asset (registered assets) ───────────────────────────────
    master_q = db.query(Asset.asset_number_code).filter(Asset.is_active == True)

    if station_id is not None:
        master_q = master_q.filter(Asset.station_id == station_id)
    elif division_id is not None:
        master_q = master_q.join(Station, Station.id == Asset.station_id)\
                           .filter(Station.division_id == division_id)
    elif zone_id is not None:
        master_q = master_q.join(Station, Station.id == Asset.station_id)\
                           .join(Division, Division.id == Station.division_id)\
                           .filter(Division.zone_id == zone_id)

    if hex_list:
        master_q = master_q.filter(Asset.asset_type_hex.in_(hex_list))

    registered = {
        row.asset_number_code
        for row in master_q.distinct().all()
        if row.asset_number_code
    }

    # ── 2. Fallback: AlertEvent for any unregistered numbers ──────────────────
    event_q = db.query(AlertEvent.asset_no).distinct()

    if station_id is not None:
        event_q = event_q.filter(AlertEvent.station_id == station_id)
    elif division_id is not None:
        event_q = event_q.join(Station, Station.id == AlertEvent.station_id)\
                         .filter(Station.division_id == division_id)
    elif zone_id is not None:
        event_q = event_q.join(Station, Station.id == AlertEvent.station_id)\
                         .join(Division, Division.id == Station.division_id)\
                         .filter(Division.zone_id == zone_id)

    if hex_list:
        event_q = event_q.filter(AlertEvent.asset_type_hex.in_(hex_list))

    for row in event_q.all():
        if row.asset_no:
            registered.add(row.asset_no)

    # ── 3. Build sorted response ─────────────────────────────────────────
    options = [
        AlertFilterOption(id=idx, label=code, value=code)
        for idx, code in enumerate(sorted(registered), start=1)
    ]
    return {
        "status": True,
        "message": "Success",
        "data": options
    }


@router.get("/causes", response_model=StandardResponse[List[AlertFilterOption]])
def list_alert_causes(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Return a list of unique causes filtered by zone, division, station, asset type, and asset number.
    """
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    q_master = db.query(AlertCauseMaster)
    hex_list = []
    if asset_type_hex:
        hex_list = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]
        if hex_list:
            q_master = q_master.filter(AlertCauseMaster.asset_type_id.in_(hex_list))

    master_causes = q_master.order_by(AlertCauseMaster.cause_code).all()
    master_codes = {c.cause_code.upper() for c in master_causes}

    q_events = db.query(AlertEvent.cause).distinct()
    if station_id is not None:
        q_events = q_events.filter(AlertEvent.station_id == station_id)
    elif division_id is not None:
        q_events = q_events.join(Station, Station.id == AlertEvent.station_id).filter(Station.division_id == division_id)
    elif zone_id is not None:
        q_events = q_events.join(Station, Station.id == AlertEvent.station_id)\
             .join(Division, Division.id == Station.division_id)\
             .filter(Division.zone_id == zone_id)

    if asset_type_hex and hex_list:
        q_events = q_events.filter(AlertEvent.asset_type_hex.in_(hex_list))

    if asset_no:
        q_events = q_events.filter(AlertEvent.asset_no == asset_no)

    event_causes = [row.cause for row in q_events.order_by(AlertEvent.cause).all() if row.cause]

    result_causes = []
    for c in master_causes:
        result_causes.append((c.cause_code, c.cause_detail))

    for code in event_causes:
        if code.upper() not in master_codes:
            result_causes.append((code, code))
            master_codes.add(code.upper())

    options = [
        AlertFilterOption(id=idx, label=detail, value=code)
        for idx, (code, detail) in enumerate(result_causes, start=1)
    ]
    return {
        "status": True,
        "message": "Success",
        "data": options
    }


@router.get("/live", response_model=StandardResponse[AlertLiveResponse])
def get_alert_live(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    zone: Optional[str] = Query(None),
    division: Optional[str] = Query(None),
    station: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db),
):
    """Return unresolved live alert cards and live counters."""
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    raw_rows = _base_live_query(
        db, zone_id, division_id, station_id, zone, division, station,
        alert_type, asset_type_hex, asset_no
    ).limit(limit).all()
    alerts = _live_cards(raw_rows)
    predictive = sum(1 for row in alerts if row.alert_type.lower() == "predictive")
    failure = sum(1 for row in alerts if row.alert_type.lower() == "failure")
    response_data = AlertLiveResponse(
        summary=AlertLiveSummary(
            predictive=predictive,
            failure=failure,
            total=len(alerts),
        ),
        alerts=alerts,
    )
    return {
        "status": True,
        "message": "Success",
        "data": response_data
    }


@router.get("/summary", response_model=StandardResponse[AlertSummaryResponse])
def get_alert_summary(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    from_time: Optional[time] = Query(None),
    to_date: Optional[date] = Query(None),
    to_time: Optional[time] = Query(None),
    view: str = Query("table", description="Frontend view mode. Currently returns table rows."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Return the Alert Summary table with the same filters shown in the UI."""
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    q = _base_summary_query(
        db, zone_id, division_id, station_id, None, None, None, alert_type,
        asset_type_hex, asset_no, cause, from_date, from_time, to_date, to_time,
    )
    summary_sq = q.subquery()
    total_rows = db.query(func.count()).select_from(summary_sq).scalar() or 0
    total_alerts = db.query(func.coalesce(func.sum(summary_sq.c.total), 0)).scalar() or 0
    total_pages, offset = _page_meta(total_rows, page, page_size)
    rows = _summary_rows(q.offset(offset).limit(page_size).all(), start=offset + 1)
    response_data = AlertSummaryResponse(
        from_time=_combine_datetime(from_date, from_time, is_end=False).isoformat()
        if from_date or from_time else None,
        to_time=_combine_datetime(to_date, to_time, is_end=True).isoformat()
        if to_date or to_time else None,
        total=int(total_alerts),
        total_rows=total_rows,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        rows=rows,
    )
    return {
        "status": True,
        "message": "Success",
        "data": response_data
    }


def _download_alert_summary_response(rows: List[AlertSummaryRow]) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "SR", "ZONE", "DIVISION", "STATION", "ALERT TYPE", "ASSET TYPE",
        "ASSET NO.", "CAUSE", "TOTAL", "TRUE", "PARTIALLY TRUE", "% (T+PT)/TOTAL",
    ])
    for row in rows:
        writer.writerow([
            row.sr,
            row.zone,
            row.division,
            row.station,
            row.alert_type,
            row.asset_type,
            row.asset_no,
            # Sanitize user-controllable free-text fields against CSV formula injection
            _csv_safe(row.cause),
            row.total,
            row.true,
            row.partially_true,
            f"{row.percentage:.1f}%",
        ])
    output.seek(0)

    filename = f"alert_summary_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary/download")
def download_alert_summary(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    from_time: Optional[time] = Query(None),
    to_date: Optional[date] = Query(None),
    to_time: Optional[time] = Query(None),
    db: Session = Depends(get_db),
):
    """Download the Alert Summary report as CSV."""
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    raw_rows = _base_summary_query(
        db, zone_id, division_id, station_id, None, None, None, alert_type,
        asset_type_hex, asset_no, cause, from_date, from_time, to_date, to_time,
    ).all()
    return _download_alert_summary_response(_summary_rows(raw_rows))


@router.get("/history", response_model=StandardResponse[AlertHistoryResponse])
def get_alert_history(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    feedback: Optional[str] = Query(None),
    alert_status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    from_time: Optional[time] = Query(None),
    to_date: Optional[date] = Query(None),
    to_time: Optional[time] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Return raw alert history rows for the Alert History table."""
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    q = _base_history_query(
        db, zone_id, division_id, station_id, None, None, None, alert_type,
        asset_type_hex, asset_no, cause, feedback, alert_status,
        from_date, from_time, to_date, to_time,
    )
    total = q.count()
    total_pages, offset = _page_meta(total, page, page_size)
    rows = _history_rows(q.offset(offset).limit(page_size).all(), start=offset + 1)
    response_data = AlertHistoryResponse(
        from_time=_combine_datetime(from_date, from_time, is_end=False).isoformat()
        if from_date or from_time else None,
        to_time=_combine_datetime(to_date, to_time, is_end=True).isoformat()
        if to_date or to_time else None,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        rows=rows,
    )
    return {
        "status": True,
        "message": "Success",
        "data": response_data
    }


@router.get("/history/download")
def download_alert_history(
    zone_id: Optional[int] = Query(None),
    division_id: Optional[int] = Query(None),
    station_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),  # Resolved via _resolve_asset_types_to_hex
    asset_no: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    feedback: Optional[str] = Query(None),
    alert_status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    from_time: Optional[time] = Query(None),
    to_date: Optional[date] = Query(None),
    to_time: Optional[time] = Query(None),
    limit: int = Query(5000, le=20000),
    db: Session = Depends(get_db),
):
    """Download the Alert History report as CSV.

    Bug fix: previously passed 18 positional args (asset_type_hex + asset_type) to
    _base_history_query which only accepts 17, shifting every downstream arg by one
    slot and causing a TypeError on every call. Fixed by resolving asset_type to a
    hex string first (matching all other history/summary endpoints) and passing
    exactly 17 args in the correct positions.
    """
    # Resolve display-group / hex / DB-id to a raw hex string, consistent with
    # every other endpoint in this file.
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)

    raw_rows = _base_history_query(
        db, zone_id, division_id, station_id, None, None, None, alert_type,
        asset_type_hex, asset_no, cause, feedback, alert_status,
        from_date, from_time, to_date, to_time,
    ).limit(limit).all()
    rows = _history_rows(raw_rows)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "SR", "ZONE", "DIVISION", "STATION", "ALERT TYPE", "ASSET TYPE",
        "ASSET NO.", "ALERT STATUS", "CAUSE", "FEEDBACK", "INCIDENCE DATE & TIME",
        "RECTIFICATION DATE & TIME", "DURATION (MIN)", "FEEDBACK DATE & TIME",
        "MAINTAINER NAME", "DESIGNATION", "MOBILE", "REMARKS",
    ])
    for row in rows:
        # Sanitize user-controllable free-text fields against CSV formula injection.
        # Fields written by operators (remark, maintainer_name, designation) may
        # contain leading =, +, -, @ which Excel/Sheets interprets as formulas.
        writer.writerow([
            row.sr,
            row.zone,
            row.division,
            row.station,
            row.alert_type,
            row.asset_type,
            row.asset_no,
            row.alert_status,
            _csv_safe(row.cause),
            _csv_safe(row.feedback) if row.feedback else "",
            row.incidence_date_time,
            row.rectification_date_time or "",
            row.duration_min if row.duration_min is not None else "",
            row.feedback_date_time or "",
            _csv_safe(row.maintainer_name) if row.maintainer_name else "",
            _csv_safe(row.designation) if row.designation else "",
            row.mobile or "",
            _csv_safe(row.remarks) if row.remarks else "",
        ])
    output.seek(0)

    filename = f"alert_history_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/filters", response_model=StandardResponse[AlertFiltersResponse])
def get_alert_filters(db: Session = Depends(get_db)):
    """Return dropdown data for the Alert Summary filter bar."""
    zones = db.query(Zone).order_by(Zone.zone_name).all()
    divisions = db.query(Division).order_by(Division.division_name).all()
    stations = db.query(Station).order_by(Station.station_name).all()
    master_causes = db.query(AlertCauseMaster).order_by(AlertCauseMaster.cause_code).all()
    master_codes = {c.cause_code.upper() for c in master_causes}

    event_causes = [
        row.cause for row in
        db.query(AlertEvent.cause).distinct().order_by(AlertEvent.cause).all()
        if row.cause
    ]

    result_causes = []
    for c in master_causes:
        result_causes.append((c.cause_code, c.cause_detail))

    for code in event_causes:
        if code.upper() not in master_codes:
            result_causes.append((code, code))
            master_codes.add(code.upper())

    cause_options_list = [
        AlertFilterOption(id=idx, label=detail, value=code)
        for idx, (code, detail) in enumerate(result_causes, start=1)
    ]
    alert_statuses = [
        row.alert_status for row in
        db.query(AlertEvent.alert_status).distinct().order_by(AlertEvent.alert_status).all()
        if row.alert_status
    ]
    asset_numbers = [
        row.asset_no for row in
        db.query(AlertEvent.asset_no).distinct().order_by(AlertEvent.asset_no).all()
        if row.asset_no
    ]

    # Fetch all masters
    all_masters = db.query(AssetTypeMaster).all()
    db_types_by_id = {t.id: t for t in all_masters}
    db_types_map = {t.asset_type_id.upper(): t for t in all_masters}

    # Set of tuples: (hex, zone_id, zone_code, zone_name, division_id, division_code, division_name, station_id, station_code, station_name)
    available_combinations = set()

    # 1. Add from Station.asset_types column
    stations_info = (
        db.query(
            Station.id.label("station_id"),
            Station.station_code.label("station_code"),
            Station.station_name.label("station_name"),
            Station.asset_types.label("station_asset_types"),
            Division.id.label("division_id"),
            Division.division_code.label("division_code"),
            Division.division_name.label("division_name"),
            Zone.id.label("zone_id"),
            Zone.zone_code.label("zone_code"),
            Zone.zone_name.label("zone_name")
        )
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
        .all()
    )

    for s in stations_info:
        at_list = s.station_asset_types
        if at_list and isinstance(at_list, list):
            for tid in at_list:
                t_master = db_types_by_id.get(tid)
                if not t_master and isinstance(tid, str):
                    t_master = db_types_map.get(tid.upper())
                if t_master:
                    available_combinations.add((
                        t_master.asset_type_id.upper(),
                        s.zone_id, s.zone_code, s.zone_name,
                        s.division_id, s.division_code, s.division_name,
                        s.station_id, s.station_code, s.station_name
                    ))

    # 2. Add from actual assets in the Asset table (as fallback / extra safety)
    asset_locations = (
        db.query(
            Asset.asset_type_hex.label("asset_type_hex"),
            Station.id.label("station_id"),
            Station.station_code.label("station_code"),
            Station.station_name.label("station_name"),
            Division.id.label("division_id"),
            Division.division_code.label("division_code"),
            Division.division_name.label("division_name"),
            Zone.id.label("zone_id"),
            Zone.zone_code.label("zone_code"),
            Zone.zone_name.label("zone_name")
        )
        .join(Station, Station.id == Asset.station_id)
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
        .distinct()
        .all()
    )

    for row in asset_locations:
        if row.asset_type_hex:
            available_combinations.add((
                row.asset_type_hex.upper(),
                row.zone_id, row.zone_code, row.zone_name,
                row.division_id, row.division_code, row.division_name,
                row.station_id, row.station_code, row.station_name
            ))

    flat_asset_types = []
    member_id = 1
    seen_combinations = set()

    for group_label, hexes in ASSET_TYPE_DISPLAY_GROUPS.items():
        # Find all available combinations matching any hex in this display group
        group_combs = []
        for h in hexes:
            t = db_types_map.get(h.upper())
            if t:
                for comb in available_combinations:
                    comb_hex = comb[0]
                    if comb_hex.upper() == h.upper():
                        group_combs.append((t, comb))

        # De-duplicate by (group_label, zone_id, division_id, station_id)
        for t, comb in group_combs:
            comb_key = (group_label, comb[1], comb[4], comb[7])
            if comb_key not in seen_combinations:
                seen_combinations.add(comb_key)
                
                # hex_id represents all hex codes in this group, separated by commas
                group_hex_str = ",".join(hexes)
                
                flat_asset_types.append(AssetTypeOption(
                    id=t.id,
                    hex_id=group_hex_str,
                    code=t.asset_type_code,
                    label=group_label,
                    group_label=group_label,
                    zone_id=comb[1],
                    zone_code=comb[2],
                    zone_name=comb[3],
                    division_id=comb[4],
                    division_code=comb[5],
                    division_name=comb[6],
                    station_id=comb[7],
                    station_code=comb[8],
                    station_name=comb[9],
                ))
                member_id += 1

    alert_types_list = [
        AlertFilterOption(id=1, label="All", value="ALL"),
        AlertFilterOption(id=2, label="Predictive", value="Predictive"),
        AlertFilterOption(id=3, label="Failure", value="Failure"),
    ]

    zones_by_id = {z.id: z for z in zones}
    divisions_by_id = {d.id: d for d in divisions}
    stations_by_id = {s.id: s for s in stations}

    # Fetch all assets from db to build the comprehensive cascading asset number list
    db_assets = (
        db.query(Asset)
        .join(Station, Station.id == Asset.station_id)
        .join(Division, Division.id == Station.division_id)
        .join(Zone, Zone.id == Division.zone_id)
        .join(AssetTypeMaster, AssetTypeMaster.asset_type_id == Asset.asset_type_hex)
        .all()
    )

    asset_numbers_map = {}
    for a in db_assets:
        key = (a.asset_number_code.upper(), a.station_id, a.asset_type_hex.upper())
        asset_numbers_map[key] = DropdownOption(
            id=a.id,
            label=a.asset_number_code,
            code=a.asset_number_code,
            hex_id=a.asset_number_id,
            value=a.asset_number_code,
            zone_id=a.station.division.zone_id,
            zone_code=a.station.division.zone.zone_code,
            zone_name=a.station.division.zone.zone_name,
            division_id=a.station.division_id,
            division_code=a.station.division.division_code,
            division_name=a.station.division.division_name,
            station_id=a.station_id,
            station_code=a.station.station_code,
            station_name=a.station.station_name,
            asset_type_id=a.asset_type.id if a.asset_type else None,
            asset_type_code=a.asset_type.asset_type_code if a.asset_type else None,
            asset_type_name=a.asset_type.asset_type_name if a.asset_type else None,
            asset_type_hex=a.asset_type_hex
        )

    # Ensure any custom/manual event assets are also in the list
    event_assets = (
        db.query(AlertEvent.asset_no, AlertEvent.station_id, AlertEvent.asset_type_hex)
        .distinct()
        .all()
    )

    idx_counter = len(db_assets) + 1
    for row in event_assets:
        if not row.asset_no:
            continue
        key = (row.asset_no.upper(), row.station_id, row.asset_type_hex.upper())
        if key not in asset_numbers_map:
            s = stations_by_id.get(row.station_id)
            d = divisions_by_id.get(s.division_id) if s else None
            z = zones_by_id.get(d.zone_id) if d else None
            t = db_types_map.get(row.asset_type_hex.upper())
            
            asset_numbers_map[key] = DropdownOption(
                id=idx_counter,
                label=row.asset_no,
                code=row.asset_no,
                hex_id="00",
                value=row.asset_no,
                zone_id=z.id if z else None,
                zone_code=z.zone_code if z else None,
                zone_name=z.zone_name if z else None,
                division_id=d.id if d else None,
                division_code=d.division_code if d else None,
                division_name=d.division_name if d else None,
                station_id=row.station_id,
                station_code=s.station_code if s else None,
                station_name=s.station_name if s else None,
                asset_type_id=t.id if t else None,
                asset_type_code=t.asset_type_code if t else None,
                asset_type_name=t.asset_type_name if t else None,
                asset_type_hex=row.asset_type_hex
            )
            idx_counter += 1

    asset_numbers_list = sorted(list(asset_numbers_map.values()), key=lambda x: x.label)

    feedbacks_list = [
        AlertFilterOption(id=idx, label=val, value=val)
        for idx, val in enumerate(FEEDBACK_OPTIONS, start=1)
    ]

    alert_statuses_list = [
        AlertFilterOption(id=idx, label=val, value=val)
        for idx, val in enumerate(alert_statuses, start=1)
    ]

    makes_inv = db.query(AssetInventory.asset_make).distinct().all()
    makes_asset = db.query(Asset.make).distinct().all()
    makes_set = set()
    for row in makes_inv:
        if row[0]:
            makes_set.add(row[0].strip())
    for row in makes_asset:
        if row[0]:
            makes_set.add(row[0].strip())
    makes = sorted(list(makes_set))
    if not makes:
        makes = ["Alstom", "Ansaldo", "CEL", "Siemens", "Kernex", "Medha"]
    asset_makes_list = [
        AlertFilterOption(id=idx, label=make, value=make)
        for idx, make in enumerate(makes, start=1)
    ]

    poll_intervals_list = [
        AlertFilterOption(id=1, label="5 sec", value="5"),
        AlertFilterOption(id=2, label="10 sec", value="10"),
        AlertFilterOption(id=3, label="15 sec", value="15"),
        AlertFilterOption(id=4, label="30 sec", value="30"),
        AlertFilterOption(id=5, label="45 sec", value="45"),
        AlertFilterOption(id=6, label="60 sec", value="60"),
    ]

    parameter_type_hexes_list = [
        AlertFilterOption(id=idx, label=f"{name} ({hex_id})", value=hex_id)
        for idx, (hex_id, (_, name, _)) in enumerate(PARAMETER_TYPE_MAP.items(), start=1)
    ]

    zones_list = [
        DropdownOption(id=z.id, label=z.zone_name, code=z.zone_code, hex_id=z.zone_id_hex, value=z.zone_code, zone_name=z.zone_name)
        for z in zones
    ]

    divisions_list = []
    for d in divisions:
        z = zones_by_id.get(d.zone_id)
        divisions_list.append(DropdownOption(
            id=d.id,
            label=d.division_name,
            code=d.division_code,
            hex_id=d.division_id_hex,
            value=d.division_code,
            zone_id=d.zone_id,
            zone_code=z.zone_code if z else None,
            zone_name=z.zone_name if z else None,
            division_name=d.division_name
        ))

    stations_list = []
    for s in stations:
        d = divisions_by_id.get(s.division_id)
        z = zones_by_id.get(d.zone_id) if d else None
        stations_list.append(DropdownOption(
            id=s.id,
            label=s.station_name,
            code=s.station_code,
            hex_id=s.station_id_hex,
            value=s.station_code,
            division_id=s.division_id,
            division_code=d.division_code if d else None,
            division_name=d.division_name if d else None,
            zone_id=d.zone_id if d else None,
            zone_code=z.zone_code if z else None,
            zone_name=z.zone_name if z else None,
            station_name=s.station_name
        ))

    roles = db.query(Role).order_by(Role.id).all()
    roles_list = [
        AlertFilterOption(id=r.id, label=r.display_name or r.name, value=str(r.id))
        for r in roles
    ]

    slave_card_types = [
        row[0] for row in db.query(SlaveCard.card_type).distinct().order_by(SlaveCard.card_type).all()
        if row[0]
    ]
    if not slave_card_types:
        slave_card_types = ["Voltage", "Analog", "DI"]

    card_types_list = [
        AlertFilterOption(id=idx, label=ct, value=ct)
        for idx, ct in enumerate(slave_card_types, start=1)
    ]

    gateways_db = db.query(Gateway).order_by(Gateway.stngw_id).all()
    gateways_list = []
    for g in gateways_db:
        s = stations_by_id.get(g.station_id) if g.station_id else None
        d = divisions_by_id.get(s.division_id) if s else None
        z = zones_by_id.get(d.zone_id) if d else None
        gateways_list.append(DropdownOption(
            id=g.id,
            label=g.stngw_id,
            code=g.stngw_id,
            hex_id=g.stngw_id,
            value=g.stngw_id,
            station_id=g.station_id,
            station_code=s.station_code if s else None,
            station_name=s.station_name if s else None,
            division_id=d.id if d else None,
            division_code=d.division_code if d else None,
            division_name=d.division_name if d else None,
            zone_id=z.id if z else None,
            zone_code=z.zone_code if z else None,
            zone_name=z.zone_name if z else None,
        ))

    # ── Slave Cards ──────────────────────────────────────────────────────────
    slave_cards_db = (
        db.query(SlaveCard)
        .join(Gateway, Gateway.id == SlaveCard.gateway_id)
        .order_by(Gateway.stngw_id, SlaveCard.card_address)
        .all()
    )
    slave_cards_list = []
    for sc in slave_cards_db:
        g = sc.gateway
        s = stations_by_id.get(g.station_id) if g and g.station_id else None
        d = divisions_by_id.get(s.division_id) if s else None
        z = zones_by_id.get(d.zone_id) if d else None
        ct = sc.card_type or ""
        label = f"{sc.card_address} ({ct})" if ct else sc.card_address
        slave_cards_list.append(SlaveCardFilterOption(
            id=sc.id,
            label=label,
            value=sc.card_address,
            card_address=sc.card_address,
            card_type=sc.card_type,
            gateway_id=sc.gateway_id,
            stngw_id=g.stngw_id if g else None,
            station_id=s.id if s else None,
            station_code=s.station_code if s else None,
            station_name=s.station_name if s else None,
            division_id=d.id if d else None,
            division_code=d.division_code if d else None,
            zone_id=z.id if z else None,
            zone_code=z.zone_code if z else None,
        ))

    # ── Channel Assignments ───────────────────────────────────────────────────
    channels_db = (
        db.query(AssetParameter)
        .filter(AssetParameter.slave_card_id.isnot(None))
        .order_by(AssetParameter.slave_card_id, AssetParameter.channel_number)
        .all()
    )
    channel_assignments_list = []
    for ch in channels_db:
        sc = ch.slave_card
        g = sc.gateway if sc else None
        s = stations_by_id.get(g.station_id) if g and g.station_id else None
        ch_label = ch.channel_number or "?"
        asset_code = ch.asset.asset_number_code if ch.asset else None
        label = f"{ch_label} → {ch.para_id}" + (f" ({asset_code})" if asset_code else "")
        channel_assignments_list.append(ChannelFilterOption(
            id=ch.id,
            label=label,
            value=ch.para_id,
            para_id=ch.para_id,
            channel_number=ch.channel_number,
            slave_card_id=ch.slave_card_id,
            card_address=sc.card_address if sc else None,
            card_type=sc.card_type if sc else None,
            gateway_id=g.id if g else None,
            stngw_id=g.stngw_id if g else None,
            station_id=s.id if s else None,
            station_code=s.station_code if s else None,
            station_name=s.station_name if s else None,
            asset_id=ch.asset_id,
            asset_number_code=asset_code,
        ))

    response_data = AlertFiltersResponse(
        zones=zones_list,
        divisions=divisions_list,
        stations=stations_list,
        alert_types=alert_types_list,
        asset_types=flat_asset_types,
        asset_numbers=asset_numbers_list,
        causes=cause_options_list,
        feedbacks=feedbacks_list,
        alert_statuses=alert_statuses_list,
        asset_makes=asset_makes_list,
        poll_intervals=poll_intervals_list,
        parameter_type_hexes=parameter_type_hexes_list,
        roles=roles_list,
        card_types=card_types_list,
        gateways=gateways_list,
        slave_cards=slave_cards_list,
        channel_assignments=channel_assignments_list,
    )
    return {
        "status": True,
        "message": "Success",
        "data": response_data
    }


@router.get("/events", response_model=StandardResponse[AlertEventsResponse])
def list_alert_events(
    station_id: Optional[int] = Query(None),
    alert_type: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    asset_no: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List raw alert events used by summary and future live/history screens."""
    q = db.query(AlertEvent)
    if station_id is not None:
        q = q.filter(AlertEvent.station_id == station_id)
    normalized_alert_type = _normalize_alert_type(alert_type)
    if normalized_alert_type:
        q = q.filter(func.lower(AlertEvent.alert_type) == normalized_alert_type.lower())
    asset_type_hex = _resolve_asset_types_to_hex(db, asset_type)
    if _blank_to_none(asset_type_hex):
        hex_list = [h.strip().upper() for h in asset_type_hex.split(",") if h.strip()]
        if len(hex_list) == 1:
            q = q.filter(AlertEvent.asset_type_hex == hex_list[0])
        elif len(hex_list) > 1:
            q = q.filter(AlertEvent.asset_type_hex.in_(hex_list))
    if _blank_to_none(asset_no):
        q = q.filter(func.lower(AlertEvent.asset_no).like(f"%{_escape_like(asset_no.lower())}%", escape="\\"))
    if _blank_to_none(cause):
        q = q.filter(func.lower(AlertEvent.cause) == cause.lower())
    total = q.count()
    total_pages, offset = _page_meta(total, page, page_size)
    rows = q.order_by(AlertEvent.alert_time.desc()).offset(offset).limit(page_size).all()
    response_data = AlertEventsResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        rows=rows,
    )
    return {
        "status": True,
        "message": "Success",
        "data": response_data
    }


def _broadcast_alert_update(record: AlertEvent):
    station_code = record.station.station_code if (record and record.station) else None
    if station_code:
        alert_data = {
            "id": record.id,
            "alert_type": record.alert_type,
            "asset_no": record.asset_no,
            "cause": record.cause,
            "cause_detail": record.remark or "",
            "time": record.alert_time.isoformat() if record.alert_time else datetime.utcnow().isoformat(),
            "alert_status": record.alert_status,
            "acknowledged": record.acknowledged
        }
        try:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    websocket_manager.broadcast_alert(
                        alert=alert_data,
                        station_code=station_code
                    )
                )
            except RuntimeError:
                import anyio
                async def run_broadcast():
                    await websocket_manager.broadcast_alert(
                        alert=alert_data,
                        station_code=station_code
                    )
                anyio.from_thread.run(run_broadcast)
        except Exception:
            pass

    safe_notify_dashboard("alert_updated")


@router.post("/events", response_model=StandardResponse[AlertEventResponse], status_code=status.HTTP_201_CREATED)
def create_alert_event(payload: AlertEventCreate, db: Session = Depends(get_db)):
    """Create an alert event that appears in Alert Summary."""
    _validate_event_payload(payload, db)

    # Check for duplicate active alert
    existing = db.query(AlertEvent).filter(
        AlertEvent.station_id == payload.station_id,
        AlertEvent.asset_no == payload.asset_no.strip(),
        AlertEvent.cause == payload.cause.strip().upper(),
        AlertEvent.alert_status == "Active"
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate alert: Already active for {payload.asset_no} - {payload.cause}"
        )

    # Suppress alert if asset is currently in active maintenance mode (unless it's a reminder alert)
    if payload.cause.strip().upper() != "MAINT-EXCEED":
        now = datetime.utcnow()
        active_maint = db.query(MaintenanceMode).filter(
            MaintenanceMode.station_id == payload.station_id,
            MaintenanceMode.asset_no == payload.asset_no,
            MaintenanceMode.is_cleared == False,
            MaintenanceMode.from_time <= now,
            MaintenanceMode.to_time >= now
        ).first()
        if active_maint:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Alert suppressed: Asset is currently in Maintenance Mode."
            )

    record = AlertEvent(
        station_id=payload.station_id,
        alert_type=payload.alert_type.strip().title(),
        asset_type_hex=payload.asset_type_hex.upper(),
        asset_no=payload.asset_no.strip(),
        cause=payload.cause.strip().upper(),
        alert_status=payload.alert_status.strip().title(),
        feedback=payload.feedback.upper() if payload.feedback else None,
        acknowledged=payload.acknowledged,
        remark=payload.remark,
        alert_time=payload.alert_time or datetime.utcnow(),
        rectification_time=payload.rectification_time,
        feedback_time=payload.feedback_time,
        maintainer_name=payload.maintainer_name,
        designation=payload.designation,
        mobile=payload.mobile,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # Track in alert engine
    from app.services.alert_engine import alert_engine, build_alert_key
    key = build_alert_key(record.station_id, record.asset_no, record.cause, record.alert_type)
    alert_engine.active_alerts[key] = {
        "alert_id": record.id,
        "timestamp": record.alert_time
    }

    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }
def _verify_alert_authorization(record: AlertEvent, user: User, db: Session):
    """
    Verify that the user is authorized to perform modifications on this alert.
    Raises HTTPException (403 Forbidden) if not permitted.
    """
    if user.role and user.role.level >= 7:
        raise HTTPException(
            status_code=403,
            detail="Guest and Auditor roles are not permitted to perform this action."
        )

    station = db.query(Station).filter(Station.id == record.station_id).first()
    if not station:
        raise HTTPException(
            status_code=404,
            detail="Station associated with this alert does not exist."
        )

    if user.division_id is not None:
        if station.division_id != user.division_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: User division does not match the alert station division."
            )

    if user.zone_id is not None:
        division = db.query(Division).filter(Division.id == station.division_id).first()
        if not division or division.zone_id != user.zone_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied: User zone does not match the alert station zone."
            )



@router.post("/{event_id}/feedback", response_model=StandardResponse[AlertEventResponse])
def update_alert_feedback(
    event_id: int,
    payload: AlertFeedbackUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set operator feedback for an alert event."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)
    if payload.feedback.upper() not in {"T", "PT", "F", "M"}:
        raise HTTPException(status_code=400, detail="feedback must be one of T, PT, F, M")

    # Annexure D §6: once a maintainer submits feedback it is locked; only
    # JE/SE/SSE or higher (role level <= 4) may modify an already-submitted
    # feedback, and the request must carry remarks explaining the change.
    if record.feedback:
        role = getattr(current_user, "role", None)
        level = getattr(role, "level", 99)
        if level > 4:
            raise HTTPException(
                status_code=403,
                detail="Feedback already submitted — only JE/SSE or higher authority can modify it"
            )
        if not payload.remarks:
            raise HTTPException(
                status_code=400,
                detail="Remarks are mandatory when modifying submitted feedback"
            )
        record.remark = payload.remarks

    record.feedback = payload.feedback.upper()
    record.feedback_time = payload.feedback_time or datetime.utcnow()

    # Auto-resolve on feedback submission
    if record.feedback in {"T", "PT", "F", "M"}:
        record.alert_status = "Cleared"
        if not record.rectification_time:
            record.rectification_time = datetime.utcnow()
            
        # Clear active alert tracking
        from app.services.alert_engine import alert_engine, build_alert_key
        key = build_alert_key(record.station_id, record.asset_no, record.cause, record.alert_type)
        if key in alert_engine.active_alerts:
            del alert_engine.active_alerts[key]
        alert_engine.alert_history[key] = datetime.utcnow()

    db.commit()
    db.refresh(record)
    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }


@router.post("/{event_id}/remark", response_model=StandardResponse[AlertEventResponse])
def update_alert_remark(
    event_id: int,
    payload: AlertRemarkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set or replace remarks for an alert event."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)

    record.remark = payload.remark
    db.commit()
    db.refresh(record)
    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }


@router.post("/{event_id}/acknowledge", response_model=StandardResponse[AlertEventResponse])
def acknowledge_alert(
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Acknowledge a live alert without clearing/rectifying it."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)

    record.acknowledged = True
    if record.alert_status.lower() != "cleared":
        record.alert_status = "Acknowledged"
    db.commit()
    db.refresh(record)
    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }


@router.post("/{event_id}/rectification", response_model=StandardResponse[AlertEventResponse])
def update_alert_rectification(
    event_id: int,
    payload: AlertRectificationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark an alert as rectified/cleared and store maintainer details."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)

    record.alert_status = payload.alert_status.strip().title()
    record.rectification_time = payload.rectification_time or datetime.utcnow()
    record.maintainer_name = payload.maintainer_name
    record.designation = payload.designation
    record.mobile = payload.mobile
    if payload.remarks is not None:
        record.remark = payload.remarks

    if record.alert_status == "Cleared":
        # Clear active alert tracking
        from app.services.alert_engine import alert_engine, build_alert_key
        key = build_alert_key(record.station_id, record.asset_no, record.cause, record.alert_type)
        if key in alert_engine.active_alerts:
            del alert_engine.active_alerts[key]
        alert_engine.alert_history[key] = datetime.utcnow()

    db.commit()
    db.refresh(record)
    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }


# Fields that are genuinely nullable on AlertEvent — only these may be set to
# null via PATCH/PUT. Required DB columns (station_id, asset_no, alert_type, …)
# are NOT in this set; sending null for them returns HTTP 400.
_NULLABLE_ALERT_FIELDS = frozenset({
    "feedback",
    "remark",
    "rectification_time",
    "feedback_time",
    "maintainer_name",
    "designation",
    "mobile",
    "escalation_level",
    "escalated_at",
})


@router.put("/events/{event_id}", response_model=StandardResponse[AlertEventResponse])
def update_alert_event(
    event_id: int,
    payload: AlertEventUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a raw alert event."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)
    _validate_event_payload(payload, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            # Guard: prevent nulling required (NOT NULL) database columns.
            if field not in _NULLABLE_ALERT_FIELDS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Field '{field}' is required and cannot be set to null.",
                )
            setattr(record, field, value)
        elif field in {"alert_type", "alert_status"}:
            setattr(record, field, value.strip().title())
        elif field in {"asset_type_hex", "cause", "feedback"}:
            setattr(record, field, value.upper())
        elif field == "asset_no":
            setattr(record, field, value.strip())
        else:
            setattr(record, field, value)

    db.commit()
    db.refresh(record)
    return {
        "status": True,
        "message": "Success",
        "data": record
    }


@router.post("/{event_id}/escalate", response_model=StandardResponse[AlertEventResponse])
def escalate_alert(
    event_id: int,
    target_level: Optional[str] = Query(None, regex="^(JE|SSE|ASTE|DSTE)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Escalate an alert event to the next hierarchy level (JE -> SSE -> ASTE -> DSTE)."""
    record = db.query(AlertEvent).filter(AlertEvent.id == event_id).first()
    if not record:
        raise HTTPException(status_code=404, detail=f"Alert event {event_id} not found")
    _verify_alert_authorization(record, current_user, db)
        
    hierarchy = ["JE", "SSE", "ASTE", "DSTE"]
    # NOTE: the isinstance check previously here was dead code — target_level is
    # typed Optional[str] with a regex validator so it can only ever be None or
    # a valid string. Removed.

    if target_level:
        escalate_to = target_level
    else:
        # Automatically escalate to next level
        current = record.escalation_level
        if not current:
            escalate_to = "JE"
        else:
            try:
                idx = hierarchy.index(current)
                if idx < len(hierarchy) - 1:
                    escalate_to = hierarchy[idx + 1]
                else:
                    escalate_to = hierarchy[-1]  # Keep at max level
            except ValueError:
                escalate_to = "JE"
                
    record.escalation_level = escalate_to
    record.escalated_at = datetime.utcnow()
    db.commit()
    db.refresh(record)
    _broadcast_alert_update(record)
    return {
        "status": True,
        "message": f"Alert escalated to {escalate_to} successfully",
        "data": record
    }

