import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List, Optional, Any, Union

from app.database import get_db
from app.services.websocket_manager import safe_notify_dashboard
from app.models.models import Gateway, Telemetry, Zone, Division, Station, AssetParameter, User
from app.models.schemas import (
    GatewayDataPayload,
    TelemetryResponse,
    GatewayResponse,
    GatewayListResponse,
    GatewayCreate,
    LinkStationRequest,
    StandardResponse,
)
from app.auth_utils import get_current_user

router = APIRouter(prefix="/gateway", tags=["Gateway Telemetry"])

# Gateway timestamp format per RDSO/SPN/257/2025 Annexure-A/B: DD-MM-YYYY HH:mm:ss.SSS
_GATEWAY_TS_FORMAT = "%d-%m-%Y %H:%M:%S.%f"


def _offset_event_timestamp(first_ts: str, sample_index: int, interval_ms: int) -> str:
    """
    Compute the timestamp of the Nth sample in an event-based (Annexure-B 5.10)
    burst, given the timestamp of the first sample and the fixed sampling
    interval. Returns the original string unchanged if it can't be parsed
    (e.g. unexpected format from a non-conforming gateway) so ingestion
    doesn't fail outright on a single malformed packet.
    """
    if sample_index == 0:
        return first_ts
    try:
        # Gateway sends milliseconds as 3 digits (.123); Python's %f wants 6.
        ts_str = first_ts.strip()
        if "." in ts_str:
            base, ms = ts_str.split(".")
            ts_str = f"{base}.{ms.ljust(6, '0')}"
        dt = datetime.strptime(ts_str, _GATEWAY_TS_FORMAT)
        dt += timedelta(milliseconds=interval_ms * sample_index)
        return dt.strftime("%d-%m-%Y %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    except (ValueError, AttributeError):
        return first_ts


def _resolve_station_from_stngw_id(stngw_id: str, db: Session) -> int | None:
    """
    Decode the 8-char stngw_id and return the matching Station.id if found.
    stngw_id format: ZZ DD SS GG
      ZZ = zone_id_hex, DD = division_id_hex, SS = station_id_hex, GG = gateway number
    """
    if len(stngw_id) != 8:
        return None
    try:
        zone_hex    = stngw_id[0:2]
        div_hex     = stngw_id[2:4]
        station_hex = stngw_id[4:6]

        zone = db.query(Zone).filter(Zone.zone_id_hex == zone_hex).first()
        if not zone:
            return None

        division = db.query(Division).filter(
            Division.zone_id == zone.id,
            Division.division_id_hex == div_hex,
        ).first()
        if not division:
            return None

        station = db.query(Station).filter(
            Station.division_id == division.id,
            Station.station_id_hex == station_hex,
        ).first()

        return station.id if station else None
    except Exception:
        return None


def _check_stngw_id_access(stngw_id: str, user: User, db: Session, action: str = "read") -> Gateway | None:
    """Verify user has access to a gateway/master card (by stngw_id) and check role levels."""
    if action == "write" and user.role and user.role.level >= 7:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest and Auditor roles are not permitted to perform this action."
        )

    gateway = db.query(Gateway).filter(Gateway.stngw_id == stngw_id.upper()).first()
    
    # Check division/zone access
    station_id = gateway.station_id if gateway else None
    if station_id is None:
        # Try to resolve station from stngw_id
        station_id = _resolve_station_from_stngw_id(stngw_id.upper(), db)
        
    if station_id is not None:
        station = db.query(Station).filter(Station.id == station_id).first()
        if not station:
            # If the resolved station isn't in DB, division/zone users shouldn't access it
            if user.division_id is not None or user.zone_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Station associated with this gateway not found."
                )
        else:
            if user.division_id is not None:
                if station.division_id != user.division_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied: User division does not match the gateway station division."
                    )
            if user.zone_id is not None:
                division = db.query(Division).filter(Division.id == station.division_id).first()
                if not division or division.zone_id != user.zone_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied: User zone does not match the gateway station zone."
                    )
    else:
        # Gateway has no station and could not resolve one
        if user.division_id is not None or user.zone_id is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Non-admin users cannot access unassigned gateways."
            )
            
    return gateway



@router.post("/data", status_code=202)
def receive_gateway_data(payload: GatewayDataPayload, db: Session = Depends(get_db)):
    """
    Receive telemetry JSON packet from a station gateway.

    Two packet types are defined in RDSO/SPN/257/2025 Annexure-B, both using
    the same para_id/prv/prt structure — they differ only in what `prt` is:

    Clause 5.9 — Fixed interval packet (every 5s, configurable), prt is an
    ARRAY of timestamps aligned 1:1 with prv:
    {
      "imei": "867409070579912",
      "stngw_id": "456523AB",
      "parameters": [
        {"para_id": "0001000C", "prv": [1.34, 1.35, 1.45, 1.46],
         "prt": ["04-11-2025 16:27:45.123", "04-11-2025 16:27:45.125",
                  "04-11-2025 16:27:45.127", "04-11-2025 16:27:45.130"]}
      ]
    }

    Clause 5.10 — Event-based packet (sent after a Point Machine/ELB
    operation completes, samples taken every 20ms/configurable), prt is a
    SINGLE timestamp string for the first sample only — later sample
    timestamps are computed by adding the sampling interval:
    {
      "imei": "867409070579912",
      "stngw_id": "456523AB",
      "parameters": [
        {"para_id": "0001000C", "prv": [1.34, 1.35, 1.45, 1.46],
         "prt": "04-11-2025 16:27:45.123"}
      ]
    }

    A bare `raw` array with no para_id (as seen in some vendor samples) is
    NOT part of Annexure-B 5.9/5.10 — such entries are stored flagged with a
    warning rather than guessed at; see the vendor before relying on this.

    On first sight of a stngw_id the gateway record is created and automatically
    linked to the correct Station by decoding the stngw_id.
    """
    stngw_id = payload.stngw_id.upper().strip()

    # Get or create gateway record
    gateway = db.query(Gateway).filter(Gateway.stngw_id == stngw_id).first()
    if not gateway:
        station_id = _resolve_station_from_stngw_id(stngw_id, db)
        gateway = Gateway(
            stngw_id=stngw_id,
            imei=payload.imei,
            station_id=station_id,
        )
        db.add(gateway)
        db.flush()
    else:
        # Update IMEI if it changed
        if gateway.imei != payload.imei:
            gateway.imei = payload.imei

        # Back-fill station_id if it was never set (e.g. station added after gateway)
        if gateway.station_id is None:
            gateway.station_id = _resolve_station_from_stngw_id(stngw_id, db)

    # Default sampling interval for event-based packets (Annexure-B 5.10).
    # Spec default is 20ms; treat as configurable per station/asset later if needed.
    EVENT_SAMPLE_INTERVAL_MS = 20

    # ── Idempotency check ──────────────────────────────────────────────────────
    # Gateways/ISP may redeliver the same packet (e.g. MQTT at-least-once
    # delivery with no ack received, or a network retry). A reading is
    # considered a duplicate if this gateway has already stored the exact
    # same (para_id, prt, prv) combination. This is an app-level check —
    # not airtight against concurrent duplicate requests landing at the same
    # instant, but sufficient for retry/redelivery duplicates, which is the
    # common case. A DB-level unique constraint + upsert is the hardening
    # step once ingestion volume/concurrency grows.
    candidate_para_ids = {
        p.para_id.upper() for p in payload.parameters if p.para_id is not None
    }
    existing_keys: set[tuple[str, str | None, float | None]] = set()
    if candidate_para_ids:
        existing_rows = (
            db.query(Telemetry.para_id, Telemetry.prt, Telemetry.prv)
            .filter(
                Telemetry.gateway_id == gateway.id,
                Telemetry.para_id.in_(candidate_para_ids),
            )
            .all()
        )
        existing_keys = {(r.para_id, r.prt, r.prv) for r in existing_rows}

    # ── Asset-parameter auto-discovery ──────────────────────────────────────────
    # On first sight of a para_id, create an unassigned AssetParameter row so
    # it shows up in the admin "Configure Slave" screen for an engineer to
    # link to an asset and set its prloc (location box). Per RDSO Annexure-A/B,
    # prloc is defined per-parameter, not per-asset, so this mapping has to
    # live at the para_id level rather than on Asset.location alone. Ingestion
    # is never blocked on this — the row is created unassigned and telemetry
    # keeps flowing regardless of whether anyone has assigned it yet.
    if candidate_para_ids:
        known_para_ids = {
            r.para_id for r in
            db.query(AssetParameter.para_id)
            .filter(AssetParameter.para_id.in_(candidate_para_ids))
            .all()
        }
        for pid in candidate_para_ids - known_para_ids:
            db.add(AssetParameter(para_id=pid, asset_id=None, prloc=None, is_assigned=False))
        if candidate_para_ids - known_para_ids:
            db.flush()

    # Store each parameter reading
    saved_count = 0
    duplicate_count = 0

    for param in payload.parameters:
        if param.raw_unattributed is not None:
            # Non-spec fallback: a bare `raw` array with no para_id was sent.
            # Annexure-B 5.10 does not define this shape — flag it rather
            # than guess which para_id it belongs to. Not deduplicated since
            # there is no para_id/prt to key on reliably.
            record = Telemetry(
                gateway_id=gateway.id,
                para_id=None,
                prv=None,
                prt=None,
                raw_payload=json.dumps({
                    "imei": payload.imei,
                    "stngw_id": stngw_id,
                    "warning": "raw array received with no para_id — not a recognized Annexure-B 5.9/5.10 shape",
                    "raw": param.raw_unattributed,
                }),
            )
            db.add(record)
            saved_count += 1
            continue

        para_id_upper = param.para_id.upper()
        is_event_based = isinstance(param.prt, str)  # 5.10: single timestamp string

        for i, value in enumerate(param.prv):
            if is_event_based:
                # Clause 5.10: prt is the timestamp of the FIRST sample only.
                # Later samples' timestamps = first_timestamp + (i * sampling interval).
                timestamp = _offset_event_timestamp(param.prt, i, EVENT_SAMPLE_INTERVAL_MS)
            else:
                # Clause 5.9: prt is an array aligned 1:1 with prv.
                timestamp = param.prt[i] if i < len(param.prt) else None

            dedup_key = (para_id_upper, timestamp, value)
            if dedup_key in existing_keys:
                duplicate_count += 1
                continue
            existing_keys.add(dedup_key)  # guard against duplicates within the same payload too

            record = Telemetry(
                gateway_id=gateway.id,
                para_id=para_id_upper,
                prv=value,
                prt=timestamp,
                raw_payload=json.dumps({
                    "imei": payload.imei,
                    "stngw_id": stngw_id,
                    "para_id": param.para_id,
                    "prv": value,
                    "prt": timestamp,
                    "packet_type": "event_based_5_10" if is_event_based else "fixed_interval_5_9",
                }),
            )
            db.add(record)
            saved_count += 1

    db.flush()
    try:
        db.commit()
    except IntegrityError:
        # Only reachable once the DB-level unique constraint
        # (uq_telemetry_gateway_para_prt_prv) is in place. Means a duplicate
        # slipped past the app-level check above — almost always a
        # concurrent retry landing at the same instant. Roll back this
        # request's writes; the original delivery already succeeded.
        db.rollback()
        res_data = {
            "status": "accepted",
            "stngw_id": stngw_id,
            "station_id": gateway.station_id,
            "records_saved": 0,
            "duplicates_skipped": saved_count + duplicate_count,
            "note": "Entire batch rolled back — a concurrent duplicate delivery was detected at the database level.",
        }
        return {
            **res_data,
            "status": True,
            "message": "Success",
            "data": res_data,
        }
    if saved_count > 0:
        safe_notify_dashboard("telemetry_ingested")

    res_data = {
        "status": "accepted",
        "stngw_id": stngw_id,
        "station_id": gateway.station_id,
        "records_saved": saved_count,
        "duplicates_skipped": duplicate_count,
    }
    return {
        **res_data,
        "status": True,
        "message": "Success",
        "data": res_data,
    }


@router.get("/data/{stngw_id}", response_model=StandardResponse[List[TelemetryResponse]])
def get_gateway_telemetry(
    stngw_id: str,
    para_id: str = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch stored telemetry readings for a gateway.
    Optionally filter by para_id.
    """
    gateway = _check_stngw_id_access(stngw_id, current_user, db, action="read")

    if not gateway:
        raise HTTPException(
            status_code=404,
            detail=f"No gateway found with stngw_id '{stngw_id}'"
        )

    query = db.query(Telemetry).filter(Telemetry.gateway_id == gateway.id)

    if para_id:
        query = query.filter(Telemetry.para_id == para_id.upper())

    rows = (
        query
        .order_by(Telemetry.received_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "status": True,
        "message": "Success",
        "data": rows
    }


@router.post("/", response_model=StandardResponse[GatewayResponse], status_code=status.HTTP_201_CREATED)
@router.post("", response_model=StandardResponse[GatewayResponse], status_code=status.HTTP_201_CREATED)
def create_gateway(
    payload: GatewayCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually provision/register a new Station Gateway.
    If station_id is not explicitly provided, attempts to auto-resolve
    the station from the 4-byte stngw_id hierarchy (Zone, Division, Station).
    """
    stngw_id = payload.stngw_id.upper().strip()

    # 1. Prevent duplicates (stngw_id is unique across Indian Railways)
    existing = db.query(Gateway).filter(Gateway.stngw_id == stngw_id).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Gateway with ID '{stngw_id}' already exists."
        )

    # 2. Station assignment
    station_id = payload.station_id
    if station_id:
        stn = db.query(Station).filter(Station.id == station_id).first()
        if not stn:
            raise HTTPException(status_code=404, detail=f"Station with ID {station_id} not found.")
    else:
        # Fallback: auto-resolve station from hex hierarchy
        station_id = _resolve_station_from_stngw_id(stngw_id, db)

    # 3. Create Gateway row
    gateway = Gateway(
        stngw_id=stngw_id,
        imei=payload.imei.strip() if payload.imei else None,
        station_id=station_id,
        mtls_cn=payload.mtls_cn.strip() if payload.mtls_cn else None,
    )
    db.add(gateway)
    db.commit()
    db.refresh(gateway)

    safe_notify_dashboard("gateway_created")

    return {
        "status": True,
        "message": "Gateway added successfully",
        "data": gateway
    }


@router.post("/{stngw_id}/link-station", response_model=StandardResponse[GatewayResponse])
def link_gateway_station(
    stngw_id: str,
    payload: Optional[LinkStationRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Link a gateway to a station.
    If payload with station_id is provided, links directly to that station.
    Otherwise attempts auto-assignment by decoding stngw_id.
    """
    gateway = _check_stngw_id_access(stngw_id, current_user, db, action="write")
    if not gateway:
        raise HTTPException(status_code=404, detail=f"Gateway '{stngw_id}' not found")

    station_id = payload.station_id if payload and payload.station_id else None
    if station_id:
        stn = db.query(Station).filter(Station.id == station_id).first()
        if not stn:
            raise HTTPException(status_code=404, detail=f"Station with ID {station_id} not found")
    else:
        station_id = _resolve_station_from_stngw_id(stngw_id.upper(), db)
        if station_id is None:
            raise HTTPException(
                status_code=422,
                detail=f"Could not resolve a station for stngw_id '{stngw_id}'. "
                       "Make sure the zone, division, and station exist in the database."
            )

    gateway.station_id = station_id
    db.commit()
    db.refresh(gateway)
    safe_notify_dashboard("gateway_updated")
    return {
        "status": True,
        "message": "Station linked successfully",
        "data": gateway
    }


@router.get("/list", response_model=StandardResponse[GatewayListResponse])
def list_gateways(
    zone_id: Optional[int] = Query(None, description="Filter by Zone ID"),
    division_id: Optional[int] = Query(None, description="Filter by Division ID"),
    station_id: Optional[int] = Query(None, description="Filter by Station ID"),
    zone: Optional[str] = Query(None, description="Filter by Zone Code or Name"),
    division: Optional[str] = Query(None, description="Filter by Division Code or Name"),
    station: Optional[str] = Query(None, description="Filter by Station Code or Name"),
    stngw_id: Optional[str] = Query(None, description="Filter by Gateway ID (stngw_id)"),
    status: Optional[str] = Query(None, description="Filter by status: Linked / Unlinked"),
    search: Optional[str] = Query(None, description="Search across Gateway ID, IMEI, or Station Code"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all registered gateways with comprehensive filtering and pagination."""
    q = db.query(Gateway)

    joined_station = False
    joined_division = False
    joined_zone = False

    def join_station():
        nonlocal q, joined_station
        if not joined_station:
            q = q.join(Station, Gateway.station_id == Station.id)
            joined_station = True

    def join_division():
        nonlocal q, joined_division
        join_station()
        if not joined_division:
            q = q.join(Division, Station.division_id == Division.id)
            joined_division = True

    def join_zone():
        nonlocal q, joined_zone
        join_division()
        if not joined_zone:
            q = q.join(Zone, Division.zone_id == Zone.id)
            joined_zone = True

    # Role-based scoping
    if current_user.division_id is not None:
        join_station()
        q = q.filter(Station.division_id == current_user.division_id)
    elif current_user.zone_id is not None:
        join_division()
        q = q.filter(Division.zone_id == current_user.zone_id)

    # Filter by IDs
    if station_id is not None and station_id != 0:
        q = q.filter(Gateway.station_id == station_id)

    if division_id is not None and division_id != 0:
        join_station()
        q = q.filter(Station.division_id == division_id)

    if zone_id is not None and zone_id != 0:
        join_division()
        q = q.filter(Division.zone_id == zone_id)

    # Filter by codes / names
    if station and station.strip() and station.strip().upper() != "ALL":
        join_station()
        stn_term = station.strip().upper()
        q = q.filter(or_(func.upper(Station.station_code) == stn_term, func.upper(Station.station_name) == stn_term))

    if division and division.strip() and division.strip().upper() != "ALL":
        join_division()
        div_term = division.strip().upper()
        q = q.filter(or_(func.upper(Division.division_code) == div_term, func.upper(Division.division_name) == div_term))

    if zone and zone.strip() and zone.strip().upper() != "ALL":
        join_zone()
        zn_term = zone.strip().upper()
        q = q.filter(or_(func.upper(Zone.zone_code) == zn_term, func.upper(Zone.zone_name) == zn_term))

    if stngw_id and stngw_id.strip() and stngw_id.strip().upper() != "ALL":
        q = q.filter(Gateway.stngw_id.ilike(f"%{stngw_id.strip()}%"))

    if status and status.strip() and status.strip().upper() != "ALL":
        st_val = status.strip().lower()
        if st_val == "linked":
            q = q.filter(Gateway.station_id.isnot(None))
        elif st_val == "unlinked":
            q = q.filter(Gateway.station_id.is_(None))

    if search and search.strip():
        join_station()
        term = f"%{search.strip()}%"
        q = q.filter(or_(
            Gateway.stngw_id.ilike(term),
            Gateway.imei.ilike(term),
            Station.station_code.ilike(term),
            Station.station_name.ilike(term),
        ))

    total = q.count()
    total_pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    rows = q.order_by(Gateway.stngw_id).offset(offset).limit(page_size).all()
    
    return {
        "status": True,
        "message": "Success",
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "rows": rows
        }
    }


@router.get("/{stngw_id}/info", response_model=StandardResponse[GatewayResponse])
def get_gateway_info(
    stngw_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get gateway registration info"""
    gateway = _check_stngw_id_access(stngw_id, current_user, db, action="read")
    if not gateway:
        raise HTTPException(status_code=404, detail=f"Gateway '{stngw_id}' not found")
    return {
        "status": True,
        "message": "Success",
        "data": gateway
    }
