from datetime import datetime
from typing import List, Optional, Any, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.models import SlaveCard, Gateway, Station, Division, Zone, AssetParameter, User
from app.models.schemas import SlaveCardCreate, SlaveCardUpdate, SlaveCardResponse, SlaveCardListResponse, StandardResponse
from app.auth_utils import get_current_user

router = APIRouter(prefix="/slave-cards", tags=["Slave Card Management"])


def _check_slave_card_ownership(slave_card_id: int, user: User, db: Session, action: str = "read") -> SlaveCard:
    """Fetch slave card and verify user has division/zone station access and write permission."""
    card = db.query(SlaveCard).filter(SlaveCard.id == slave_card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail=f"Slave Card {slave_card_id} not found")

    # If action is write, block Guest / Auditor roles (level >= 7)
    if action == "write" and user.role and user.role.level >= 7:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest and Auditor roles are not permitted to perform this action."
        )

    # Check division/zone access
    gateway = db.query(Gateway).filter(Gateway.id == card.gateway_id).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway associated with this slave card not found")

    station = db.query(Station).filter(Station.id == gateway.station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station associated with this slave card not found")

    if user.division_id is not None:
        if station.division_id != user.division_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: User division does not match the slave card station division."
            )

    if user.zone_id is not None:
        division = db.query(Division).filter(Division.id == station.division_id).first()
        if not division or division.zone_id != user.zone_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: User zone does not match the slave card station zone."
            )

    return card


def _check_gateway_ownership(gateway_id: int, user: User, db: Session, action: str = "read"):
    """Verify user is authorized to access/modify a gateway/master card."""
    # If action is write, block Guest / Auditor roles (level >= 7)
    if action == "write" and user.role and user.role.level >= 7:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest and Auditor roles are not permitted to perform this action."
        )

    gateway = db.query(Gateway).filter(Gateway.id == gateway_id).first()
    if not gateway:
        raise HTTPException(status_code=404, detail=f"Gateway with ID {gateway_id} not found")

    station = db.query(Station).filter(Station.id == gateway.station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station associated with this gateway not found")

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


@router.get("", response_model=StandardResponse[SlaveCardListResponse])
def list_slave_cards(
    gateway_id: Optional[int] = Query(None, description="Filter by Gateway ID"),
    stngw_id: Optional[str] = Query(None, description="Filter by Gateway Code (stngw_id)"),
    card_address: Optional[str] = Query(None, description="Filter by Card Address (e.g. '81')"),
    card_type: Optional[str] = Query(None, description="Filter by Card Type (e.g. 'Voltage', 'Analog', 'DI')"),
    station_id: Optional[int] = Query(None, description="Filter by Station ID"),
    division_id: Optional[int] = Query(None, description="Filter by Division ID"),
    zone_id: Optional[int] = Query(None, description="Filter by Zone ID"),
    search: Optional[str] = Query(None, description="Search term across card address, card type, gateway code, or station name"),
    q: Optional[str] = Query(None, description="Alias for search term"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    limit: Optional[int] = Query(None, ge=1, le=500, description="Alias for page_size"),
    offset: Optional[int] = Query(None, ge=0, description="Row offset for pagination"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all configured Slave Cards with comprehensive filtering and pagination."""
    q_db = db.query(SlaveCard)

    search_term = search or q

    joined_gateway = False
    joined_station = False
    joined_division = False

    def join_gateway():
        nonlocal q_db, joined_gateway
        if not joined_gateway:
            q_db = q_db.join(Gateway, SlaveCard.gateway_id == Gateway.id)
            joined_gateway = True

    def join_station():
        nonlocal q_db, joined_station
        join_gateway()
        if not joined_station:
            q_db = q_db.join(Station, Gateway.station_id == Station.id)
            joined_station = True

    def join_division():
        nonlocal q_db, joined_division
        join_station()
        if not joined_division:
            q_db = q_db.join(Division, Station.division_id == Division.id)
            joined_division = True

    # First restrict list to user division/zone
    if current_user.division_id is not None:
        join_station()
        q_db = q_db.filter(Station.division_id == current_user.division_id)
    elif current_user.zone_id is not None:
        join_division()
        q_db = q_db.filter(Division.zone_id == current_user.zone_id)

    if gateway_id is not None:
        q_db = q_db.filter(SlaveCard.gateway_id == gateway_id)

    if stngw_id:
        join_gateway()
        q_db = q_db.filter(Gateway.stngw_id.ilike(f"%{stngw_id.strip()}%"))

    if card_address:
        q_db = q_db.filter(SlaveCard.card_address.ilike(f"%{card_address.strip()}%"))

    if card_type:
        q_db = q_db.filter(SlaveCard.card_type.ilike(f"%{card_type.strip()}%"))

    if station_id is not None:
        join_gateway()
        q_db = q_db.filter(Gateway.station_id == station_id)

    if division_id is not None:
        join_station()
        q_db = q_db.filter(Station.division_id == division_id)

    if zone_id is not None:
        join_division()
        q_db = q_db.filter(Division.zone_id == zone_id)

    if search_term:
        join_gateway()
        term = f"%{search_term.strip()}%"
        q_db = q_db.filter(
            or_(
                SlaveCard.card_address.ilike(term),
                SlaveCard.card_type.ilike(term),
                Gateway.stngw_id.ilike(term),
            )
        )

    total = q_db.count()

    effective_page_size = limit if limit is not None else page_size
    if offset is not None:
        calc_offset = offset
        calc_page = (offset // effective_page_size) + 1
    else:
        calc_page = page
        calc_offset = (page - 1) * effective_page_size

    total_pages = (total + effective_page_size - 1) // effective_page_size if total else 0

    rows = q_db.order_by(SlaveCard.id).offset(calc_offset).limit(effective_page_size).all()

    return {
        "status": True,
        "message": "Success",
        "data": SlaveCardListResponse(
            total=total,
            page=calc_page,
            page_size=effective_page_size,
            total_pages=total_pages,
            rows=rows,
        )
    }


@router.get("/{slave_card_id}", response_model=StandardResponse[SlaveCardResponse])
def get_slave_card(
    slave_card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve details of a single Slave Card."""
    card = _check_slave_card_ownership(slave_card_id, current_user, db, action="read")
    return {
        "status": True,
        "message": "Success",
        "data": card
    }


@router.post("", response_model=StandardResponse[SlaveCardResponse], status_code=status.HTTP_201_CREATED)
def create_slave_card(
    payload: SlaveCardCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add/configure a new Slave Card under a Master Card/Gateway."""
    # 1. Validate Gateway exists and check user's ownership
    _check_gateway_ownership(payload.gateway_id, current_user, db, action="write")
    
    # 2. Uppercase card address (e.g. "81" or "8A")
    card_addr = payload.card_address.strip().upper()
    
    # 3. Check for unique constraint violation: (gateway_id, card_address, card_type)
    existing = db.query(SlaveCard).filter(
        SlaveCard.gateway_id == payload.gateway_id,
        SlaveCard.card_address == card_addr,
        SlaveCard.card_type == payload.card_type,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Slave Card with address '{card_addr}' and type '{payload.card_type}' already configured under Gateway {payload.gateway_id}"
        )
        
    card = SlaveCard(
        gateway_id=payload.gateway_id,
        card_address=card_addr,
        card_type=payload.card_type,
    )
    db.add(card)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Slave Card unique constraint violation."
        )
    db.refresh(card)
    return {
        "status": True,
        "message": "Success",
        "data": card
    }


@router.put("/{slave_card_id}", response_model=StandardResponse[SlaveCardResponse])
def update_slave_card(
    slave_card_id: int,
    payload: SlaveCardUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update configurations of a Slave Card."""
    card = _check_slave_card_ownership(slave_card_id, current_user, db, action="write")
        
    data = payload.model_dump(exclude_unset=True)
    
    if "gateway_id" in data:
        _check_gateway_ownership(data["gateway_id"], current_user, db, action="write")
            
    if "card_address" in data:
        data["card_address"] = data["card_address"].strip().upper()
        
    # Check uniqueness if modifying unique keys
    new_gw = data.get("gateway_id", card.gateway_id)
    new_addr = data.get("card_address", card.card_address)
    new_type = data.get("card_type", card.card_type)
    
    if new_gw != card.gateway_id or new_addr != card.card_address or new_type != card.card_type:
        existing = db.query(SlaveCard).filter(
            SlaveCard.gateway_id == new_gw,
            SlaveCard.card_address == new_addr,
            SlaveCard.card_type == new_type,
            SlaveCard.id != slave_card_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Another Slave Card with address '{new_addr}' and type '{new_type}' exists under Gateway {new_gw}"
            )
            
    for field, value in data.items():
        setattr(card, field, value)
        
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Slave Card unique constraint violation."
        )
    db.refresh(card)
    return {
        "status": True,
        "message": "Success",
        "data": card
    }


@router.delete("/{slave_card_id}", status_code=status.HTTP_200_OK, response_model=StandardResponse[None])
def delete_slave_card(
    slave_card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a Slave Card. Referenced Channels will have their slave_card_id set to NULL."""
    card = _check_slave_card_ownership(slave_card_id, current_user, db, action="write")

    stngw_id = card.gateway.stngw_id if card.gateway else str(slave_card_id)
    card_address = card.card_address

    # Unlink Channels referencing this slave card
    db.query(AssetParameter).filter(AssetParameter.slave_card_id == slave_card_id).update(
        {"slave_card_id": None}
    )

    db.delete(card)
    db.commit()

    return {
        "status": True,
        "message": f"Slave card '{stngw_id}/{card_address}' deleted successfully",
        "data": None,
    }

