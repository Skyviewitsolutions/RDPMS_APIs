from datetime import datetime, UTC
from typing import List, Optional, Tuple, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
import re

from app.models.models import (
    AssetParameter,
    SlaveCard,
    Gateway,
    Station,
    Division,
    Zone,
    Asset,
    User,
)
from app.models.schemas import (
    ChannelAssignmentCreate,
    ChannelAssignmentUpdate,
    ChannelAssignmentResponse,
    ChannelAssignmentListResponse,
    AvailableChannelItem,
    AvailableChannelsResponse,
)


def normalize_channel(ch: str) -> str:
    """Normalize channel string, e.g. 'ch1' -> 'CH1', '1' -> 'CH1', 'CH-1' -> 'CH1'."""
    cleaned = ch.strip().upper()
    cleaned = cleaned.replace(" ", "").replace("-", "").replace("_", "")
    if cleaned.isdigit():
        return f"CH{cleaned}"
    if cleaned.startswith("CH"):
        return cleaned
    return f"CH{cleaned}"


def get_channel_index(ch: str) -> int:
    """Extract numeric index from channel string e.g. 'CH3' -> 3."""
    digits = re.findall(r"\d+", ch)
    if digits:
        return int(digits[0])
    return 1


def get_card_channel_count(card_type: Optional[str]) -> int:
    """Determine hardware channel capacity based on card type."""
    if not card_type:
        return 12
    ct = card_type.strip().upper()
    if "DI" in ct or "DIGITAL" in ct:
        return 16
    if "VOLT" in ct:
        return 12
    if "ANALOG" in ct or "CURRENT" in ct:
        return 12
    return 12


def check_assignment_access(
    ap: AssetParameter, user: User, db: Session, action: str = "read"
) -> None:
    """Verify user has appropriate division/zone permissions and write authorization."""
    if action == "write" and user.role and user.role.level >= 7:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest and Auditor roles are not permitted to perform this action.",
        )

    # Resolve station
    station = None
    if ap.slave_card and ap.slave_card.gateway and ap.slave_card.gateway.station:
        station = ap.slave_card.gateway.station
    elif ap.asset and ap.asset.station:
        station = ap.asset.station

    if not station:
        return

    if user.division_id is not None and station.division_id != user.division_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: User division does not match the assignment station division.",
        )

    if user.zone_id is not None:
        division = db.query(Division).filter(Division.id == station.division_id).first()
        if not division or division.zone_id != user.zone_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: User zone does not match the assignment station zone.",
            )


def build_assignment_response(ap: AssetParameter, db: Session) -> ChannelAssignmentResponse:
    """Construct ChannelAssignmentResponse with full hierarchy details."""
    slave_card = ap.slave_card
    asset = ap.asset
    gateway = slave_card.gateway if slave_card else (asset.gateway if asset else None)
    station = (
        (gateway.station if (gateway and gateway.station) else None)
        or (asset.station if asset else None)
    )
    division = station.division if (station and station.division) else None
    zone = division.zone if (division and division.zone) else None

    # stngw_id: fallback to asset.station_gateway_id if gateway object is not loaded or missing
    stngw_id = None
    if gateway and gateway.stngw_id:
        stngw_id = gateway.stngw_id
    elif asset and asset.station_gateway_id:
        stngw_id = asset.station_gateway_id

    # Card number label: determine sequence of this card within its gateway
    card_number_str = None
    if slave_card and gateway:
        sibling_ids = [
            c.id
            for c in db.query(SlaveCard.id)
            .filter(SlaveCard.gateway_id == gateway.id)
            .order_by(SlaveCard.id)
            .all()
        ]
        if slave_card.id in sibling_ids:
            idx = sibling_ids.index(slave_card.id) + 1
            card_number_str = f"Card {idx}"
        else:
            card_number_str = f"Card #{slave_card.id}"

    ch_norm = ap.channel_number or None
    resolved_loc = ap.prloc or (asset.location if asset else None)

    return ChannelAssignmentResponse(
        id=ap.id,
        zone_id=zone.id if zone else None,
        zone_code=zone.zone_code if zone else None,
        zone_name=zone.zone_name if zone else None,
        division_id=division.id if division else None,
        division_code=division.division_code if division else None,
        division_name=division.division_name if division else None,
        station_id=station.id if station else None,
        station_code=station.station_code if station else None,
        station_name=station.station_name if station else None,
        gateway_id=gateway.id if gateway else None,
        stngw_id=stngw_id,
        slave_card_id=slave_card.id if slave_card else None,
        card_number=card_number_str,
        card_address=slave_card.card_address if slave_card else None,
        card_type=slave_card.card_type if slave_card else None,
        channel=ch_norm,
        channel_number=ch_norm,
        para_id=ap.para_id,
        asset_id=asset.id if asset else None,
        asset_number_code=asset.asset_number_code if asset else None,
        asset_name=asset.smms_asset_name if asset else None,
        prloc=resolved_loc,
        location_box=resolved_loc,
        is_assigned=ap.is_assigned,
        created_at=ap.created_at,
        updated_at=ap.updated_at,
    )


class ChannelAssignmentService:
    @staticmethod
    def list_assignments(
        db: Session,
        user: User,
        zone_id: Optional[int] = None,
        division_id: Optional[int] = None,
        station_id: Optional[int] = None,
        gateway_id: Optional[int] = None,
        stngw_id: Optional[str] = None,
        slave_card_id: Optional[int] = None,
        card_address: Optional[str] = None,
        card_type: Optional[str] = None,
        channel_number: Optional[str] = None,
        is_assigned: Optional[bool] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> ChannelAssignmentListResponse:
        """Query channel assignments with eager loading and user access scopes."""
        # Base query with joined loads
        q = (
            db.query(AssetParameter)
            .outerjoin(SlaveCard, SlaveCard.id == AssetParameter.slave_card_id)
            .outerjoin(Gateway, Gateway.id == SlaveCard.gateway_id)
            .outerjoin(Station, Station.id == Gateway.station_id)
            .outerjoin(Division, Division.id == Station.division_id)
            .outerjoin(Zone, Zone.id == Division.zone_id)
            .outerjoin(Asset, Asset.id == AssetParameter.asset_id)
            .options(
                joinedload(AssetParameter.slave_card).joinedload(SlaveCard.gateway).joinedload(Gateway.station).joinedload(Station.division).joinedload(Division.zone),
                joinedload(AssetParameter.asset).joinedload(Asset.gateway),
                joinedload(AssetParameter.asset).joinedload(Asset.station).joinedload(Station.division).joinedload(Division.zone),
            )
        )

        # Enforce user role-based division/zone scopes
        if user.division_id is not None:
            q = q.filter(
                or_(
                    Station.division_id == user.division_id,
                    Asset.station_id.in_(
                        db.query(Station.id).filter(Station.division_id == user.division_id)
                    ),
                )
            )

        if user.zone_id is not None:
            q = q.filter(Division.zone_id == user.zone_id)

        # Filters
        if zone_id is not None:
            q = q.filter(Division.zone_id == zone_id)

        if division_id is not None:
            q = q.filter(Station.division_id == division_id)

        if station_id is not None:
            q = q.filter(or_(Gateway.station_id == station_id, Asset.station_id == station_id))

        if gateway_id is not None:
            q = q.filter(SlaveCard.gateway_id == gateway_id)

        if stngw_id:
            q = q.filter(Gateway.stngw_id.ilike(f"%{stngw_id.strip()}%"))

        if slave_card_id is not None:
            q = q.filter(AssetParameter.slave_card_id == slave_card_id)

        if card_address:
            q = q.filter(SlaveCard.card_address.ilike(f"%{card_address.strip()}%"))

        if card_type:
            q = q.filter(SlaveCard.card_type.ilike(f"%{card_type.strip()}%"))

        if channel_number:
            norm_ch = normalize_channel(channel_number)
            q = q.filter(
                or_(
                    AssetParameter.channel_number.ilike(f"%{channel_number.strip()}%"),
                    AssetParameter.channel_number.ilike(f"%{norm_ch}%"),
                )
            )

        if is_assigned is not None:
            q = q.filter(AssetParameter.is_assigned == is_assigned)
        else:
            # By default on Channel Assignment table, only show rows with channel configured or assigned
            q = q.filter(
                or_(
                    AssetParameter.is_assigned == True,
                    AssetParameter.slave_card_id.isnot(None),
                )
            )

        if search:
            term = f"%{search.strip()}%"
            q = q.filter(
                or_(
                    AssetParameter.para_id.ilike(term),
                    Asset.asset_number_code.ilike(term),
                    Asset.smms_asset_name.ilike(term),
                    AssetParameter.prloc.ilike(term),
                    Gateway.stngw_id.ilike(term),
                    Station.station_name.ilike(term),
                    Station.station_code.ilike(term),
                )
            )

        total = q.count()
        total_pages = (total + page_size - 1) // page_size if total else 0
        offset = (page - 1) * page_size
        rows = q.order_by(AssetParameter.updated_at.desc(), AssetParameter.id.desc()).offset(offset).limit(page_size).all()

        return ChannelAssignmentListResponse(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            rows=[build_assignment_response(r, db) for r in rows],
        )

    @staticmethod
    def get_assignment(db: Session, assignment_id: int, user: User) -> ChannelAssignmentResponse:
        """Fetch a single channel assignment by ID with authorization check."""
        ap = (
            db.query(AssetParameter)
            .options(
                joinedload(AssetParameter.slave_card).joinedload(SlaveCard.gateway).joinedload(Gateway.station),
                joinedload(AssetParameter.asset),
            )
            .filter(AssetParameter.id == assignment_id)
            .first()
        )
        if not ap:
            raise HTTPException(status_code=404, detail=f"Channel assignment {assignment_id} not found")

        check_assignment_access(ap, user, db, action="read")
        return build_assignment_response(ap, db)

    @staticmethod
    def get_available_channels(
        db: Session, slave_card_id: int, user: User, exclude_assignment_id: Optional[int] = None
    ) -> AvailableChannelsResponse:
        """List channels for a slave card with their active assignment status."""
        card = db.query(SlaveCard).filter(SlaveCard.id == slave_card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail=f"Slave card {slave_card_id} not found")

        # Check access on card's station
        if card.gateway and card.gateway.station:
            stn = card.gateway.station
            if user.division_id is not None and stn.division_id != user.division_id:
                raise HTTPException(status_code=403, detail="Access denied for this division")
            if user.zone_id is not None and stn.division and stn.division.zone_id != user.zone_id:
                raise HTTPException(status_code=403, detail="Access denied for this zone")

        card_capacity = get_card_channel_count(card.card_type)

        # Existing parameter rows under this card
        existing_params = (
            db.query(AssetParameter)
            .options(joinedload(AssetParameter.asset))
            .filter(AssetParameter.slave_card_id == slave_card_id)
            .all()
        )

        param_map: Dict[str, AssetParameter] = {}
        max_idx = card_capacity
        for p in existing_params:
            if p.channel_number:
                norm = normalize_channel(p.channel_number)
                param_map[norm] = p
                idx = get_channel_index(norm)
                if idx > max_idx:
                    max_idx = idx

        items: List[AvailableChannelItem] = []
        assigned_count = 0

        for i in range(1, max_idx + 1):
            ch_name = f"CH{i}"
            ap = param_map.get(ch_name)
            is_active_assigned = False
            cur_para_id = None
            cur_asset_id = None
            cur_asset_code = None
            cur_prloc = None

            if ap:
                cur_para_id = ap.para_id
                cur_prloc = ap.prloc
                if ap.asset:
                    cur_asset_id = ap.asset_id
                    cur_asset_code = ap.asset.asset_number_code
                if ap.is_assigned and ap.asset_id is not None:
                    if exclude_assignment_id is not None and ap.id == exclude_assignment_id:
                        is_active_assigned = False
                    else:
                        is_active_assigned = True
                        assigned_count += 1

            items.append(
                AvailableChannelItem(
                    channel=ch_name,
                    channel_number=ch_name,
                    channel_index=i,
                    is_assigned=is_active_assigned,
                    current_para_id=cur_para_id,
                    current_asset_id=cur_asset_id,
                    current_asset_number_code=cur_asset_code,
                    current_prloc=cur_prloc,
                )
            )

        available_count = len(items) - assigned_count

        return AvailableChannelsResponse(
            slave_card_id=card.id,
            card_address=card.card_address,
            card_type=card.card_type,
            total_channels=len(items),
            assigned_count=assigned_count,
            available_count=available_count,
            channels=items,
        )

    @staticmethod
    def create_assignment(
        db: Session, payload: ChannelAssignmentCreate, user: User
    ) -> ChannelAssignmentResponse:
        """Create or assign a channel on a Slave Card to an Asset."""
        # 1. Permission check
        if user.role and user.role.level >= 7:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest and Auditor roles are not permitted to create channel assignments.",
            )

        # 2. Validate Slave Card
        card = db.query(SlaveCard).options(joinedload(SlaveCard.gateway)).filter(SlaveCard.id == payload.slave_card_id).first()
        if not card:
            raise HTTPException(status_code=404, detail=f"Slave Card {payload.slave_card_id} not found")

        gateway = card.gateway
        if not gateway:
            raise HTTPException(status_code=400, detail="Slave Card has no associated Gateway")

        if payload.gateway_id is not None and payload.gateway_id != gateway.id:
            raise HTTPException(status_code=400, detail=f"Card does not belong to Gateway {payload.gateway_id}")

        # 3. Check station division/zone ownership
        stn = gateway.station
        if stn:
            if user.division_id is not None and stn.division_id != user.division_id:
                raise HTTPException(status_code=403, detail="Access denied: Card belongs to another division")
            if user.zone_id is not None and stn.division and stn.division.zone_id != user.zone_id:
                raise HTTPException(status_code=403, detail="Access denied: Card belongs to another zone")

        # 4. Validate Asset
        asset = db.query(Asset).filter(Asset.id == payload.asset_id).first()
        if not asset:
            raise HTTPException(status_code=404, detail=f"Asset {payload.asset_id} not found")

        # Verify asset station matches gateway station
        if gateway.station_id and asset.station_id != gateway.station_id:
            raise HTTPException(
                status_code=400,
                detail=f"Asset station (ID: {asset.station_id}) does not match Gateway station (ID: {gateway.station_id})",
            )

        norm_channel = normalize_channel(payload.channel_number)

        # 5. Check duplicate active assignment on this card & channel
        existing_active = (
            db.query(AssetParameter)
            .filter(
                AssetParameter.slave_card_id == card.id,
                AssetParameter.channel_number == norm_channel,
                AssetParameter.is_assigned == True,
            )
            .first()
        )
        if existing_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Channel '{norm_channel}' on Slave Card {card.id} (Addr: {card.card_address}) is already assigned to asset '{existing_active.asset.asset_number_code if existing_active.asset else existing_active.asset_id}'",
            )

        # 6. Check if an unassigned row already exists for (card.id, norm_channel) -> REUSE it!
        existing_unassigned = (
            db.query(AssetParameter)
            .filter(
                AssetParameter.slave_card_id == card.id,
                AssetParameter.channel_number == norm_channel,
                AssetParameter.is_assigned == False,
            )
            .first()
        )

        # Determine para_id
        target_para_id = payload.para_id.strip().upper() if payload.para_id else None

        if target_para_id:
            # Check if this para_id is used elsewhere
            existing_with_pid = (
                db.query(AssetParameter).filter(AssetParameter.para_id == target_para_id).first()
            )
            if existing_with_pid and existing_unassigned and existing_with_pid.id != existing_unassigned.id:
                if existing_with_pid.is_assigned:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Parameter ID '{target_para_id}' is already assigned to another channel (ID: {existing_with_pid.id})",
                    )
                else:
                    # Merge unassigned rows: delete the orphan and keep target
                    db.delete(existing_unassigned)
                    existing_unassigned = existing_with_pid
            elif existing_with_pid and not existing_unassigned:
                if existing_with_pid.is_assigned:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Parameter ID '{target_para_id}' is already assigned to another channel (ID: {existing_with_pid.id})",
                    )
                else:
                    existing_unassigned = existing_with_pid

        if existing_unassigned:
            # REUSE existing record
            existing_unassigned.asset_id = asset.id
            existing_unassigned.prloc = payload.prloc.strip() if payload.prloc else None
            existing_unassigned.is_assigned = True
            existing_unassigned.slave_card_id = card.id
            existing_unassigned.channel_number = norm_channel
            if target_para_id:
                existing_unassigned.para_id = target_para_id
            existing_unassigned.updated_at = datetime.now(UTC)
            target_record = existing_unassigned
        else:
            # If target_para_id was not provided, auto-generate RDSO compliant 8-char hex
            if not target_para_id:
                # Format: AssetTypeHex(2) + AssetNumberIdHex(2) + CardAddr(2) + ChannelIndexHex(2)
                ch_idx = get_channel_index(norm_channel)
                base_pid = f"{asset.asset_type_hex[:2]}{asset.asset_number_id[:2]}{card.card_address[-2:]}{ch_idx:02X}".upper()
                candidate_pid = base_pid
                counter = 1
                while db.query(AssetParameter).filter(AssetParameter.para_id == candidate_pid).first():
                    candidate_pid = f"{asset.asset_type_hex[:2]}{asset.asset_number_id[:2]}{(ch_idx + counter) % 256:02X}{counter:02X}".upper()
                    counter += 1
                target_para_id = candidate_pid

            target_record = AssetParameter(
                para_id=target_para_id,
                slave_card_id=card.id,
                channel_number=norm_channel,
                asset_id=asset.id,
                prloc=payload.prloc.strip() if payload.prloc else None,
                is_assigned=True,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(target_record)

        try:
            db.commit()
            db.refresh(target_record)
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Database constraint violation while saving channel assignment: {str(e.orig) if hasattr(e, 'orig') else str(e)}",
            )

        return build_assignment_response(target_record, db)

    @staticmethod
    def update_assignment(
        db: Session, assignment_id: int, payload: ChannelAssignmentUpdate, user: User
    ) -> ChannelAssignmentResponse:
        """Update an existing channel assignment."""
        ap = db.query(AssetParameter).filter(AssetParameter.id == assignment_id).first()
        if not ap:
            raise HTTPException(status_code=404, detail=f"Channel assignment {assignment_id} not found")

        check_assignment_access(ap, user, db, action="write")

        data = payload.model_dump(exclude_unset=True)

        if "asset_id" in data and data["asset_id"] is not None:
            asset = db.query(Asset).filter(Asset.id == data["asset_id"]).first()
            if not asset:
                raise HTTPException(status_code=404, detail=f"Asset {data['asset_id']} not found")
            ap.asset_id = asset.id

        target_card_id = ap.slave_card_id
        if "slave_card_id" in data and data["slave_card_id"] is not None:
            card = db.query(SlaveCard).filter(SlaveCard.id == data["slave_card_id"]).first()
            if not card:
                raise HTTPException(status_code=404, detail=f"Slave Card {data['slave_card_id']} not found")
            ap.slave_card_id = card.id
            target_card_id = card.id

        if "channel_number" in data and data["channel_number"] is not None:
            norm_ch = normalize_channel(data["channel_number"])
            if target_card_id and (norm_ch != ap.channel_number or ("slave_card_id" in data and data["slave_card_id"] != ap.slave_card_id)):
                # Check for active conflict on the same card
                existing_active = (
                    db.query(AssetParameter)
                    .filter(
                        AssetParameter.slave_card_id == target_card_id,
                        AssetParameter.channel_number == norm_ch,
                        AssetParameter.is_assigned == True,
                        AssetParameter.id != assignment_id,
                    )
                    .first()
                )
                if existing_active:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Channel '{norm_ch}' is already assigned to asset '{existing_active.asset.asset_number_code if existing_active.asset else existing_active.asset_id}' on this card",
                    )
            ap.channel_number = norm_ch

        if "para_id" in data and data["para_id"] is not None:
            target_pid = data["para_id"].strip().upper()
            if target_pid != ap.para_id:
                conflict = (
                    db.query(AssetParameter)
                    .filter(AssetParameter.para_id == target_pid, AssetParameter.id != assignment_id)
                    .first()
                )
                if conflict:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Parameter ID '{target_pid}' is already used by assignment {conflict.id}",
                    )
                ap.para_id = target_pid

        if "prloc" in data:
            ap.prloc = data["prloc"].strip() if data["prloc"] else None

        if "is_assigned" in data and data["is_assigned"] is not None:
            ap.is_assigned = data["is_assigned"]
        elif ap.asset_id is not None and ap.slave_card_id is not None and ap.channel_number is not None:
            ap.is_assigned = True

        ap.updated_at = datetime.now(UTC)

        try:
            db.commit()
            db.refresh(ap)
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Database constraint violation on update: {str(e.orig) if hasattr(e, 'orig') else str(e)}",
            )

        return build_assignment_response(ap, db)

    @staticmethod
    def delete_assignment(db: Session, assignment_id: int, user: User) -> bool:
        """Unassign a channel assignment, retaining telemetry record or deleting unassigned entry."""
        ap = db.query(AssetParameter).filter(AssetParameter.id == assignment_id).first()
        if not ap:
            raise HTTPException(status_code=404, detail=f"Channel assignment {assignment_id} not found")

        check_assignment_access(ap, user, db, action="write")

        # Unassign asset mapping so the channel becomes immediately available again for reuse
        ap.asset_id = None
        ap.prloc = None
        ap.is_assigned = False
        ap.updated_at = datetime.now(UTC)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=500, detail="Failed to unassign channel")

        return True
