from __future__ import annotations

from pydantic import BaseModel, Field, AliasChoices, model_validator, field_validator
from typing import List, Optional, Union, Generic, TypeVar
from datetime import datetime
from enum import Enum as PyEnum



# ─── Zone ─────────────────────────────────────────────────────────────────────

class ZoneBase(BaseModel):
    zone_name: str = Field(validation_alias=AliasChoices('zone_name', 'zoneName', 'name'))
    zone_code: str = Field(validation_alias=AliasChoices('zone_code', 'zoneCode'))
    zone_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('zone_id_hex', 'zoneIdHex'))
    headquarters: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "Active"

class ZoneCreate(ZoneBase):
    pass

class ZoneUpdate(BaseModel):
    zone_name: Optional[str] = Field(default=None, validation_alias=AliasChoices('zone_name', 'zoneName', 'name'))
    zone_code: Optional[str] = Field(default=None, validation_alias=AliasChoices('zone_code', 'zoneCode'))
    zone_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('zone_id_hex', 'zoneIdHex'))
    headquarters: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

class ZoneResponse(ZoneBase):
    id: int
    zoneName: str = ""
    name: str = ""
    zoneCode: str = ""

    @model_validator(mode="after")
    def populate_aliases(self) -> "ZoneResponse":
        self.zoneName = self.zone_name
        self.name = self.zone_name
        self.zoneCode = self.zone_code
        return self

    class Config:
        from_attributes = True

class ZoneWithDivisions(ZoneResponse):
    divisions: List["DivisionResponse"] = []


# ─── Division ─────────────────────────────────────────────────────────────────

class DivisionBase(BaseModel):
    division_name: str = Field(validation_alias=AliasChoices('division_name', 'divisionName', 'name'))
    division_code: str = Field(validation_alias=AliasChoices('division_code', 'divisionCode'))
    division_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('division_id_hex', 'divisionIdHex'))
    zone_id: Optional[int] = None
    zone: Optional[str] = None
    headquarters: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "Active"

class DivisionCreate(DivisionBase):
    pass

class DivisionUpdate(BaseModel):
    division_name: Optional[str] = Field(default=None, validation_alias=AliasChoices('division_name', 'divisionName', 'name'))
    division_code: Optional[str] = Field(default=None, validation_alias=AliasChoices('division_code', 'divisionCode'))
    division_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('division_id_hex', 'divisionIdHex'))
    zone_id: Optional[int] = None
    zone: Optional[str] = None
    headquarters: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

class DivisionResponse(BaseModel):
    id: int
    division_name: str
    division_code: str
    division_id_hex: str
    zone_id: int
    headquarters: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "Active"

    divisionName: str = ""
    name: str = ""
    divisionCode: str = ""
    zoneCode: str = ""
    zone: str = ""

    @model_validator(mode="before")
    @classmethod
    def pre_validate(cls, data: any) -> any:
        if not isinstance(data, dict):
            zone_code = getattr(data.zone, "zone_code", "") if getattr(data, "zone", None) else ""
            res = {
                "id": data.id,
                "division_name": data.division_name,
                "division_code": data.division_code,
                "division_id_hex": data.division_id_hex,
                "zone_id": data.zone_id,
                "headquarters": data.headquarters,
                "description": data.description,
                "status": data.status or "Active",
                "divisionName": data.division_name,
                "name": data.division_name,
                "divisionCode": data.division_code,
                "zoneCode": zone_code,
                "zone": zone_code,
            }
            if hasattr(data, "stations"):
                res["stations"] = data.stations
            return res
        else:
            data["divisionName"] = data.get("division_name", data.get("divisionName", ""))
            data["name"] = data.get("division_name", data.get("name", ""))
            data["divisionCode"] = data.get("division_code", data.get("divisionCode", ""))
            z_val = data.get("zone", "")
            if isinstance(z_val, str):
                data["zoneCode"] = data.get("zoneCode", z_val)
            return data

    class Config:
        from_attributes = True

class ZoneMinimalResponse(BaseModel):
    id: int
    zone_name: str
    zone_code: str
    zoneName: str = ""
    zoneCode: str = ""
    name: str = ""

    @model_validator(mode="after")
    def populate_aliases(self) -> "ZoneMinimalResponse":
        self.zoneName = self.zone_name
        self.zoneCode = self.zone_code
        self.name = self.zone_name
        return self

    class Config:
        from_attributes = True

class DivisionMinimalResponse(BaseModel):
    id: int
    division_name: str
    division_code: str
    zone_id: int
    divisionName: str = ""
    divisionCode: str = ""
    name: str = ""

    @model_validator(mode="after")
    def populate_aliases(self) -> "DivisionMinimalResponse":
        self.divisionName = self.division_name
        self.divisionCode = self.division_code
        self.name = self.division_name
        return self

    class Config:
        from_attributes = True

class StationMinimalResponse(BaseModel):
    id: int
    station_name: str
    station_code: str
    division_id: int
    stationName: str = ""
    stationCode: str = ""
    name: str = ""

    @model_validator(mode="after")
    def populate_aliases(self) -> "StationMinimalResponse":
        self.stationName = self.station_name
        self.stationCode = self.station_code
        self.name = self.station_name
        return self

    class Config:
        from_attributes = True

class DivisionWithStations(DivisionResponse):
    stations: List["StationResponse"] = []


# ─── Station ──────────────────────────────────────────────────────────────────

class StationBase(BaseModel):
    station_name: str = Field(validation_alias=AliasChoices('station_name', 'stationName', 'name'))
    station_code: str = Field(validation_alias=AliasChoices('station_code', 'stationCode'))
    station_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('station_id_hex', 'stationIdHex'))
    division_id: Optional[int] = None
    division: Optional[str] = None
    zone: Optional[str] = None
    category: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "Active"
    asset_types: Optional[List[int]] = Field(default=None, validation_alias=AliasChoices('asset_types', 'assetTypes'))

class StationCreate(StationBase):
    pass

class StationUpdate(BaseModel):
    station_name: Optional[str] = Field(default=None, validation_alias=AliasChoices('station_name', 'stationName', 'name'))
    station_code: Optional[str] = Field(default=None, validation_alias=AliasChoices('station_code', 'stationCode'))
    station_id_hex: Optional[str] = Field(default=None, validation_alias=AliasChoices('station_id_hex', 'stationIdHex'))
    division_id: Optional[int] = None
    division: Optional[str] = None
    zone: Optional[str] = None
    category: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    asset_types: Optional[List[int]] = Field(default=None, validation_alias=AliasChoices('asset_types', 'assetTypes'))

class StationResponse(BaseModel):
    id: int
    station_name: str
    station_code: str
    station_id_hex: str
    division_id: int
    category: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "Active"
    asset_types: Optional[List[int]] = []

    stationCode: str = ""
    stationName: str = ""
    name: str = ""
    zone: str = ""
    division: str = ""

    @model_validator(mode="before")
    @classmethod
    def pre_validate(cls, data: any) -> any:
        if not isinstance(data, dict):
            div_code = getattr(data.division, "division_code", "") if getattr(data, "division", None) else ""
            zone_code = getattr(data.division.zone, "zone_code", "") if (getattr(data, "division", None) and getattr(data.division, "zone", None)) else ""
            return {
                "id": data.id,
                "station_name": data.station_name,
                "station_code": data.station_code,
                "station_id_hex": data.station_id_hex,
                "division_id": data.division_id,
                "category": data.category,
                "address": data.address,
                "description": data.description,
                "status": data.status or "Active",
                "asset_types": data.asset_types or [],
                "stationCode": data.station_code,
                "stationName": data.station_name,
                "name": data.station_name,
                "division": div_code,
                "zone": zone_code,
            }
        else:
            data["stationCode"] = data.get("station_code", data.get("stationCode", ""))
            data["stationName"] = data.get("station_name", data.get("stationName", ""))
            data["name"] = data.get("station_name", data.get("name", ""))
            d_val = data.get("division", "")
            if isinstance(d_val, str):
                data["division"] = d_val
            z_val = data.get("zone", "")
            if isinstance(z_val, str):
                data["zone"] = z_val
            return data

    class Config:
        from_attributes = True


# ─── Gateway ──────────────────────────────────────────────────────────────────

class GatewayCreate(BaseModel):
    stngw_id: str = Field(..., description="4-Byte hexadecimal station gateway ID (8 characters e.g. 01011200)")
    imei: Optional[str] = Field(None, max_length=20, description="Modem IMEI string (optional)")
    station_id: Optional[int] = Field(None, description="Station ID this gateway belongs to (optional)")
    mtls_cn: Optional[str] = Field(None, max_length=200, description="mTLS certificate Common Name (optional)")

    @field_validator("stngw_id")
    @classmethod
    def validate_stngw_id(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if len(cleaned) != 8 or not all(c in "0123456789ABCDEF" for c in cleaned):
            raise ValueError("Gateway ID must be exactly 8 hexadecimal characters (0-9, A-F)")
        return cleaned

    @field_validator("imei")
    @classmethod
    def validate_imei(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not (10 <= len(cleaned) <= 20 and cleaned.isdigit()):
            raise ValueError("IMEI must be a valid numeric string between 10 and 20 digits")
        return cleaned


class DecodedHierarchyItem(BaseModel):
    id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    hex: str


class GatewayHierarchyPreviewResponse(BaseModel):
    stngw_id: str
    zone_hex: str
    division_hex: str
    station_hex: str
    gateway_number_hex: str
    gateway_number: int
    is_valid: bool
    can_register: bool
    resolved_station_id: Optional[int] = None
    zone: Optional[DecodedHierarchyItem] = None
    division: Optional[DecodedHierarchyItem] = None
    station: Optional[DecodedHierarchyItem] = None
    error: Optional[str] = None


class LinkStationRequest(BaseModel):
    station_id: Optional[int] = None
    imei: Optional[str] = None

    @field_validator("imei")
    @classmethod
    def validate_imei(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not (10 <= len(cleaned) <= 20 and cleaned.isdigit()):
            raise ValueError("IMEI must be a valid numeric string between 10 and 20 digits")
        return cleaned


class GatewayUpdate(BaseModel):
    imei: Optional[str] = None
    station_id: Optional[int] = None

    @field_validator("imei")
    @classmethod
    def validate_imei(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            return None
        if not (10 <= len(cleaned) <= 20 and cleaned.isdigit()):
            raise ValueError("IMEI must be a valid numeric string between 10 and 20 digits")
        return cleaned


class GatewayResponse(BaseModel):
    id: int
    stngw_id: str
    imei: Optional[str] = None
    station_id: Optional[int] = None
    created_at: Optional[datetime] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    station_id_hex: Optional[str] = None
    division_id: Optional[int] = None
    division_code: Optional[str] = None
    division_name: Optional[str] = None
    division_id_hex: Optional[str] = None
    zone_id: Optional[int] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    zone_id_hex: Optional[str] = None
    gateway_number: Optional[int] = None
    gateway_number_hex: Optional[str] = None
    status: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def pre_validate(cls, data: any) -> any:
        if not isinstance(data, dict):
            stn = getattr(data, "station", None)
            div = getattr(stn, "division", None) if stn else None
            zn = getattr(div, "zone", None) if div else None

            st_id = getattr(data, "station_id", None)
            st_code = getattr(stn, "station_code", None) if stn else None
            st_name = getattr(stn, "station_name", None) if stn else None
            st_hex = getattr(stn, "station_id_hex", None) if stn else None
            d_id = getattr(div, "id", None) if div else None
            d_code = getattr(div, "division_code", None) if div else None
            d_name = getattr(div, "division_name", None) if div else None
            d_hex = getattr(div, "division_id_hex", None) if div else None
            z_id = getattr(zn, "id", None) if zn else None
            z_code = getattr(zn, "zone_code", None) if zn else None
            z_name = getattr(zn, "zone_name", None) if zn else None
            z_hex = getattr(zn, "zone_id_hex", None) if zn else None
            status_val = "Linked" if st_id is not None else "Unlinked"

            gw_id_str = getattr(data, "stngw_id", "") or ""
            gw_num = None
            gw_hex = None
            if len(gw_id_str) == 8:
                try:
                    gw_hex = gw_id_str[6:8]
                    gw_num = int(gw_hex, 16)
                except ValueError:
                    pass

            return {
                "id": int(data.id) if data.id is not None else 0,
                "stngw_id": data.stngw_id,
                "imei": data.imei,
                "station_id": int(st_id) if st_id is not None else None,
                "created_at": data.created_at,
                "station_code": st_code,
                "station_name": st_name,
                "station_id_hex": st_hex,
                "division_id": int(d_id) if d_id is not None else None,
                "division_code": d_code,
                "division_name": d_name,
                "division_id_hex": d_hex,
                "zone_id": int(z_id) if z_id is not None else None,
                "zone_code": z_code,
                "zone_name": z_name,
                "zone_id_hex": z_hex,
                "gateway_number": gw_num,
                "gateway_number_hex": gw_hex,
                "status": status_val,
            }
        else:
            st_id = data.get("station_id")
            if "status" not in data:
                data["status"] = "Linked" if st_id is not None else "Unlinked"
            gw_id_str = data.get("stngw_id", "") or ""
            if len(gw_id_str) == 8 and data.get("gateway_number") is None:
                try:
                    data["gateway_number_hex"] = gw_id_str[6:8]
                    data["gateway_number"] = int(gw_id_str[6:8], 16)
                except ValueError:
                    pass
            return data

    class Config:
        from_attributes = True


class GatewayListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[GatewayResponse]


# ─── Telemetry (Gateway Ingestion) ────────────────────────────────────────────

class ParameterPayload(BaseModel):
    """
    A single entry in the gateway `parameters[]` array.

    Per RDSO/SPN/257/2025 Annexure-B, two packet types share this same
    para_id/prv/prt structure — they differ only in what `prt` looks like:

    Clause 5.9 — Parameter data packet on fixed intervals (Normal):
        para_id is a single hex string.
        prv is an array of every value change within the interval.
        prt is an ARRAY of timestamps, one per value in prv (DD-MM-YYYY HH:mm:ss.SSS).

    Clause 5.10 — Parameter data packet after completing an event (event-based,
    e.g. Point Machine / ELB current/voltage during operation):
        para_id is a single hex string (same field — always present per spec).
        prv is an array of all samples taken during the event (e.g. every 20ms).
        prt is a SINGLE timestamp string — the time of the FIRST sample only.
        Every subsequent sample's time = prt + (sample_index * sampling_interval_ms),
        where sampling_interval_ms defaults to 20ms but is configurable.

    NOTE: the spec does not use a bare `raw` array with no para_id. If a
    vendor payload sends `raw` without para_id, that is a deviation from
    Annexure-B 5.10 and should be raised with the vendor/gateway team rather
    than silently accepted — see `raw_unattributed` below.
    """
    para_id: Optional[str] = None
    prv: Optional[List[float]] = None
    prt: Optional[Union[str, List[str]]] = None  # str (5.10 event) or List[str] (5.9 fixed-interval)

    # Non-spec fallback only — present if a payload sends a bare `raw` array
    # with no para_id, which Annexure-B 5.10 does not define. Kept so such
    # payloads don't 422 outright, but should be treated as a data-quality
    # flag, not a supported format.
    raw_unattributed: Optional[List[float]] = Field(default=None, alias="raw")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def check_shape(self) -> "ParameterPayload":
        has_spec_shape = self.para_id is not None or self.prv is not None or self.prt is not None
        has_raw_fallback = self.raw_unattributed is not None
        if not has_spec_shape and not has_raw_fallback:
            raise ValueError("parameters[] entry must contain either para_id/prv/prt (Annexure-B 5.9/5.10) or raw")
        if has_spec_shape and (self.para_id is None or self.prv is None or self.prt is None):
            raise ValueError("para_id, prv, and prt must all be present together (Annexure-B 5.9/5.10)")
        return self

    @property
    def timestamps(self) -> List[Optional[str]]:
        """
        Normalize prt into a list aligned with prv, regardless of which
        clause (5.9 fixed-interval array, or 5.10 event-based single string)
        produced this entry.

        For 5.10 (prt is a single string — first-sample time only), this
        does NOT compute the +20ms offsets for later samples — it returns
        the first timestamp for index 0 and None for the rest. Callers that
        need the calculated per-sample time should add the configured
        sampling interval (default 20ms) themselves; see gateway.py.
        """
        if self.prv is None:
            return []
        if isinstance(self.prt, list):
            return self.prt
        # single string (5.10) — only the first sample's time is known directly
        return [self.prt] + [None] * (len(self.prv) - 1)

class GatewayDataPayload(BaseModel):
    imei: str
    stngw_id: str
    parameters: List[ParameterPayload]

class TelemetryResponse(BaseModel):
    id: int
    gateway_id: int
    para_id: str
    prv: Optional[float]
    prt: Optional[str]
    received_at: datetime
    class Config:
        from_attributes = True


class TelemetryPoint(BaseModel):
    """Single time-series data point used in chart responses."""
    t: str            # ISO timestamp string (prt from gateway, or received_at)
    v: Optional[float]


class TelemetrySeriesResponse(BaseModel):
    """
    A single parameter series for one asset — returned by the telemetry query endpoint.
    Contains everything the frontend needs to render a chart panel.
    """
    para_id: str
    asset_type_hex: str
    asset_type_name: Optional[str]
    asset_type_code: Optional[str]
    asset_number_hex: str               # bytes 2-3 of para_id
    parameter_type_hex: str             # bytes 4-5 of para_id
    parameter_name: Optional[str]       # e.g. "Peak Current"
    parameter_unit: Optional[str]       # e.g. "A"
    representation: Optional[str]       # e.g. "Maximum"
    stngw_id: str
    data: List[TelemetryPoint]
    latest_value: Optional[float]
    threshold_warning_low: Optional[float] = None
    threshold_warning_high: Optional[float] = None
    threshold_critical_low: Optional[float] = None
    threshold_critical_high: Optional[float] = None


class TelemetryQueryResponse(BaseModel):
    """Top-level response for GET /telemetry — groups series by asset."""
    station_id: Optional[int]
    station_name: Optional[str]
    asset_type_hex: Optional[str]
    asset_number: Optional[str]
    from_time: Optional[str]
    to_time: Optional[str]
    series: List[TelemetrySeriesResponse]

# ─── Telemetry History ────────────────────────────────────────────────────────

class TelemetryHistoryRow(BaseModel):
    """One timestamp row in the history table — values keyed by column label."""
    timestamp: str                          # e.g. "2026-06-04T12:10:00"
    asset_number_hex: str                   # e.g. "01"
    stngw_id: str
    values: dict                            # e.g. {"I_AVG (A)": 4.3, "I_PEAK (A)": 5.9}


class TelemetryHistoryColumn(BaseModel):
    """Metadata for one column in the history table."""
    key: str                                # dict key used in TelemetryHistoryRow.values
    parameter_name: str                     # e.g. "Avg Current"
    parameter_unit: str                     # e.g. "A"
    parameter_type_hex: str                 # e.g. "01"
    threshold_warning_low: Optional[float] = None
    threshold_warning_high: Optional[float] = None
    threshold_critical_low: Optional[float] = None
    threshold_critical_high: Optional[float] = None


class TelemetryHistoryResponse(BaseModel):
    Zone: Optional[str] = None
    Division: Optional[str] = None
    Asset_Type: Optional[str] = None
    Asset_No: Optional[str] = None
    Time: Optional[str] = None
    Status: Optional[str] = "Predictive"
    live_data: Optional[List[dict]] = None
    station_id: Optional[int]
    station_name: Optional[str]
    asset_number: Optional[str]
    from_time: Optional[str]
    to_time: Optional[str]
    columns: List[TelemetryHistoryColumn]   # ordered list of columns for table headers
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[TelemetryHistoryRow]
# ─── Assets ───────────────────────────────────────────────────────────────────

class AssetTypeOption(BaseModel):
    """One entry in the Asset Type dropdown."""
    id: int
    hex_id: str          # e.g. "00"
    code: str            # e.g. "EOP"
    label: str           # e.g. "Point Machine"
    group_label: str     # display group for the UI, e.g. "Point Machine"
    zone_id: Optional[int] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    division_id: Optional[int] = None
    division_code: Optional[str] = None
    division_name: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None


class AssetTypeGroupOption(BaseModel):
    """
    Grouped option for the dashboard Asset Type dropdown.
    Each group maps to one of the friendly labels in ASSET_TYPE_DISPLAY_GROUPS.
    """
    id: int
    group_label: str
    asset_type_hexes: List[str]
    members: List[AssetTypeOption]


class ParameterTypeOption(BaseModel):
    """One entry in a parameter type listing."""
    hex_id: str          # e.g. "02"
    code: str            # e.g. "PEAK_CUR"
    label: str           # e.g. "Peak Current"
    unit: str            # e.g. "A"


class ParameterReprOption(BaseModel):
    """One entry in the representation listing."""
    hex_id: str
    code: str
    label: str


# ─── Asset Inventory / Detail ─────────────────────────────────────────────────

class AssetInventoryBase(BaseModel):
    station_id: int
    asset_type_hex: str
    asset_make: str
    count: int

class AssetInventoryCreate(AssetInventoryBase):
    pass

class AssetInventoryUpdate(BaseModel):
    station_id: Optional[int] = None
    asset_type_hex: Optional[str] = None
    asset_make: Optional[str] = None
    count: Optional[int] = None

class AssetInventoryResponse(AssetInventoryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    asset_type: str = ""
    class Config:
        from_attributes = True

class AssetMakeOption(BaseModel):
    id: int
    label: str
    value: str


class AssetFiltersResponse(BaseModel):
    zones: List[DropdownOption]
    divisions: List[DropdownOption]
    stations: List[DropdownOption]
    asset_types: List[AssetTypeOption]
    asset_makes: List[AssetMakeOption]
    roles: List[AlertFilterOption] = []

class AssetDetailRow(BaseModel):
    id: int
    sr: int
    zone_id: int
    zone: Optional[ZoneMinimalResponse] = None
    division_id: int
    division: Optional[DivisionMinimalResponse] = None
    station_id: int
    station: Optional[StationMinimalResponse] = None
    asset_type_hex: str
    asset_type: str
    asset_make: str
    count: int

class AssetDetailResponse(BaseModel):
    as_on: str
    total: int
    rows: List[AssetDetailRow]

class DropdownOption(BaseModel):
    id: int
    label: str
    code: str
    hex_id: str
    value: Optional[str] = None
    zone_id: Optional[int] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    division_id: Optional[int] = None
    division_code: Optional[str] = None
    division_name: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    asset_type_id: Optional[int] = None
    asset_type_code: Optional[str] = None
    asset_type_name: Optional[str] = None
    asset_type_hex: Optional[str] = None

class ZoneDropdownResponse(BaseModel):
    zones: List[DropdownOption]

class DivisionDropdownResponse(BaseModel):
    divisions: List[DropdownOption]

class StationDropdownResponse(BaseModel):
    stations: List[DropdownOption]

# ─── Alert Summary ────────────────────────────────────────────────────────────

class AlertEventBase(BaseModel):
    station_id: int
    alert_type: str
    asset_type_hex: str
    asset_no: str
    cause: str
    alert_status: str = "Active"
    feedback: Optional[str] = None
    acknowledged: bool = False
    remark: Optional[str] = None
    alert_time: Optional[datetime] = None
    rectification_time: Optional[datetime] = None
    feedback_time: Optional[datetime] = None
    maintainer_name: Optional[str] = None
    designation: Optional[str] = None
    mobile: Optional[str] = None


class AlertEventCreate(AlertEventBase):
    pass


class AlertEventUpdate(BaseModel):
    alert_type: Optional[str] = None
    asset_type_hex: Optional[str] = None
    asset_no: Optional[str] = None
    cause: Optional[str] = None
    alert_status: Optional[str] = None
    feedback: Optional[str] = None
    acknowledged: Optional[bool] = None
    remark: Optional[str] = None
    alert_time: Optional[datetime] = None
    rectification_time: Optional[datetime] = None
    feedback_time: Optional[datetime] = None
    maintainer_name: Optional[str] = None
    designation: Optional[str] = None
    mobile: Optional[str] = None


class AlertEventResponse(BaseModel):
    id: int
    station_id: int
    alert_type: str
    asset_type_hex: str
    asset_no: str
    cause: str
    alert_status: str
    feedback: Optional[str]
    acknowledged: bool
    remark: Optional[str]
    alert_time: datetime
    rectification_time: Optional[datetime]
    feedback_time: Optional[datetime]
    maintainer_name: Optional[str]
    designation: Optional[str]
    mobile: Optional[str]
    escalation_level: Optional[str] = None
    escalated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True


class AlertHistoryRow(BaseModel):
    sr: int
    id: int
    zone_id: int
    zone: str
    division_id: int
    division: str
    station_id: int
    station: str
    alert_type: str
    asset_type_hex: str
    asset_type: str
    asset_no: str
    alert_status: str
    cause: str
    feedback: Optional[str]
    incidence_date_time: str
    rectification_date_time: Optional[str]
    duration_min: Optional[float]
    feedback_date_time: Optional[str]
    maintainer_name: Optional[str]
    designation: Optional[str]
    mobile: Optional[str]
    remarks: Optional[str]


class AlertHistoryResponse(BaseModel):
    from_time: Optional[str]
    to_time: Optional[str]
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[AlertHistoryRow]


class AlertLiveSummary(BaseModel):
    predictive: int
    failure: int
    total: int


class AlertLiveCard(BaseModel):
    id: int
    zone_id: int
    zone: str
    division_id: int
    division: str
    station_id: int
    station: str
    title: str
    alert_type: str
    asset_type_hex: str
    asset_type: str
    asset_no: str
    alert_status: str
    cause: str
    feedback: Optional[str]
    acknowledged: bool
    incidence_date_time: str
    remarks: Optional[str]


class AlertLiveResponse(BaseModel):
    summary: AlertLiveSummary
    alerts: List[AlertLiveCard]


class AlertFeedbackUpdate(BaseModel):
    feedback: str
    feedback_time: Optional[datetime] = None
    # Mandatory (Annexure D §6) when a JE/SSE+ modifies already-submitted feedback
    remarks: Optional[str] = None


class AlertRemarkUpdate(BaseModel):
    remark: str


class AlertRectificationUpdate(BaseModel):
    rectification_time: Optional[datetime] = None
    alert_status: str = "Cleared"
    maintainer_name: Optional[str] = None
    designation: Optional[str] = None
    mobile: Optional[str] = None
    remarks: Optional[str] = None


class AlertSummaryRow(BaseModel):
    sr: int
    zone_id: int
    zone: str
    division_id: int
    division: str
    station_id: int
    station: str
    alert_type: str
    asset_type_hex: str
    asset_type: str
    asset_no: str
    cause: str
    total: int
    true: int
    partially_true: int
    percentage: float


class AlertSummaryResponse(BaseModel):
    from_time: Optional[str]
    to_time: Optional[str]
    total: int
    total_rows: int
    page: int
    page_size: int
    total_pages: int
    rows: List[AlertSummaryRow]


class AlertEventsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[AlertEventResponse]


class AlertFilterOption(BaseModel):
    id: int
    label: str
    value: str


class SlaveCardFilterOption(BaseModel):
    """Lightweight slave card option for universal filter dropdowns."""
    id: int
    label: str          # e.g. "81 (Voltage)"
    value: str          # card_address e.g. "81"
    card_address: str
    card_type: Optional[str] = None
    gateway_id: Optional[int] = None
    stngw_id: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    division_id: Optional[int] = None
    division_code: Optional[str] = None
    zone_id: Optional[int] = None
    zone_code: Optional[str] = None


class ChannelFilterOption(BaseModel):
    """Lightweight channel assignment option for universal filter dropdowns."""
    id: int
    label: str          # e.g. "CH1 → P001A01020102"
    value: str          # para_id
    para_id: str
    channel_number: Optional[str] = None
    slave_card_id: Optional[int] = None
    card_address: Optional[str] = None
    card_type: Optional[str] = None
    gateway_id: Optional[int] = None
    stngw_id: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    asset_id: Optional[int] = None
    asset_number_code: Optional[str] = None


class AlertFiltersResponse(BaseModel):
    zones: List[DropdownOption]
    divisions: List[DropdownOption]
    stations: List[DropdownOption]
    alert_types: List[AlertFilterOption]
    asset_types: List[AssetTypeOption]
    asset_numbers: List[DropdownOption]
    causes: List[AlertFilterOption]
    feedbacks: List[AlertFilterOption]
    alert_statuses: List[AlertFilterOption]
    asset_makes: List[AlertFilterOption] = []
    poll_intervals: List[AlertFilterOption] = []
    parameter_type_hexes: List[AlertFilterOption] = []
    roles: List[AlertFilterOption] = []
    card_types: List[AlertFilterOption] = []
    gateways: List[DropdownOption] = []
    slave_cards: List[SlaveCardFilterOption] = []
    channel_assignments: List[ChannelFilterOption] = []


# ─── Thresholds ───────────────────────────────────────────────────────────────

class ThresholdBase(BaseModel):
    asset_type_hex: str
    parameter_type_hex: str
    station_id: Optional[int] = None
    warning_low: Optional[float] = None
    warning_high: Optional[float] = None
    critical_low: Optional[float] = None
    critical_high: Optional[float] = None
    unit: Optional[str] = None
    description: Optional[str] = None

class ThresholdCreate(ThresholdBase):
    pass

class ThresholdUpdate(BaseModel):
    warning_low: Optional[float] = None
    warning_high: Optional[float] = None
    critical_low: Optional[float] = None
    critical_high: Optional[float] = None
    unit: Optional[str] = None
    description: Optional[str] = None

class ThresholdResponse(ThresholdBase):
    id: int
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True


# ─── Decode ───────────────────────────────────────────────────────────────────

class GatewayDecodeResponse(BaseModel):
    stngw_id: str
    zone_id_hex: str
    division_id_hex: str
    station_id_hex: str
    gateway_number_hex: str
    zone_name: Optional[str] = None
    zone_code: Optional[str] = None
    division_name: Optional[str] = None
    division_code: Optional[str] = None
    station_name: Optional[str] = None
    station_code: Optional[str] = None

class ParaDecodeResponse(BaseModel):
    para_id: str
    asset_type_id_hex: str
    asset_number_id_hex: str
    parameter_type_id_hex: str
    parameter_representation_id_hex: str
    asset_type_name: Optional[str] = None
    asset_type_code: Optional[str] = None
    parameter_name: Optional[str] = None
    parameter_unit: Optional[str] = None
    representation: Optional[str] = None


# ─── Dropdown ─────────────────────────────────────────────────────────────────


# ─── Asset (assets table) ─────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    """Register a new physical asset instance (RDSO/SPN/257/2025 Annexure A, Page 40)."""
    smms_asset_code:    str                         # unique SMMS code (g)
    smms_asset_name:    str                         # SMMS name (i)
    asset_number_code:  str                         # label at station e.g. PT-101 (h)
    asset_number_id:    str                         # 1-byte hex 00-FF (f)
    asset_type_hex:     str                         # e.g. "00"
    station_gateway_id: str                         # 8-char stngw_id FK
    station_id:         int
    make:               Optional[str] = None
    model:              Optional[str] = None
    attr1:              Optional[str] = None        # sub-asset type / custom attr
    attr2:              Optional[str] = None
    location:           Optional[str] = None
    is_active:          bool = True


class AssetUpdate(BaseModel):
    """Partial update — all fields optional."""
    smms_asset_code:    Optional[str] = None
    smms_asset_name:    Optional[str] = None
    asset_number_code:  Optional[str] = None
    asset_number_id:    Optional[str] = None
    asset_type_hex:     Optional[str] = None
    station_gateway_id: Optional[str] = None
    station_id:         Optional[int] = None
    make:               Optional[str] = None
    model:              Optional[str] = None
    attr1:              Optional[str] = None
    attr2:              Optional[str] = None
    location:           Optional[str] = None
    is_active:          Optional[bool] = None


class AssetResponse(BaseModel):
    id:                 int
    smms_asset_code:    str
    smms_asset_name:    str
    asset_number_code:  str
    asset_number_id:    str
    asset_type_hex:     str
    asset_type_name:    Optional[str] = None        # resolved
    asset_type_code:    Optional[str] = None        # resolved
    station_gateway_id: str
    station_id:         int
    station_code:       Optional[str] = None        # resolved
    station_name:       Optional[str] = None        # resolved
    make:               Optional[str] = None
    model:              Optional[str] = None
    attr1:              Optional[str] = None
    attr2:              Optional[str] = None
    location:           Optional[str] = None
    is_active:          bool
    created_at:         datetime
    updated_at:         datetime

    class Config:
        from_attributes = True


class AssetListResponse(BaseModel):
    total:       int
    page:        int
    page_size:   int
    total_pages: int
    rows:        List[AssetResponse]


# ─── Asset Parameter (para_id → asset + prloc mapping) ────────────────────────

class AssetParameterUpdate(BaseModel):
    """
    Used by the admin 'Configure Slave' screen to assign a discovered
    para_id to an asset and record its physical location box (prloc).
    Matches the vendor flow: 'After initial save of channel config: Add
    asset_number_code, Add prloc, Save'.
    """
    asset_id: Optional[int] = Field(None, validation_alias=AliasChoices('asset_id', 'assetId'))
    prloc: Optional[str] = Field(default=None, max_length=50, description="Location box, e.g. 'LB-01'")
    slave_card_id: Optional[int] = Field(None, validation_alias=AliasChoices('slave_card_id', 'slaveCardId'))
    channel_number: Optional[str] = Field(None, max_length=10, validation_alias=AliasChoices('channel_number', 'channelNumber'))


class AssetParameterResponse(BaseModel):
    id: int
    para_id: str
    asset_id: Optional[int] = None
    asset_number_code: Optional[str] = None   # resolved, e.g. "PT-101"
    asset_type_hex: Optional[str] = None      # resolved, decoded from para_id bytes 0-1
    parameter_type_hex: Optional[str] = None  # resolved, decoded from para_id bytes 4-5
    parameter_name: Optional[str] = None      # resolved, e.g. "Peak Current"
    prloc: Optional[str] = None
    is_assigned: bool
    station_id: Optional[int] = None          # resolved via asset, if assigned
    station_code: Optional[str] = None        # resolved via asset, if assigned
    slave_card_id: Optional[int] = None
    channel_number: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    slaveCardId: Optional[int] = None
    channelNumber: Optional[str] = None
    assetId: Optional[int] = None
    assetNumberCode: Optional[str] = None
    assetTypeHex: Optional[str] = None
    parameterTypeHex: Optional[str] = None
    parameterName: Optional[str] = None
    stationId: Optional[int] = None
    stationCode: Optional[str] = None

    @model_validator(mode="after")
    def populate_aliases(self) -> "AssetParameterResponse":
        self.slaveCardId = self.slave_card_id
        self.channelNumber = self.channel_number
        self.assetId = self.asset_id
        self.assetNumberCode = self.asset_number_code
        self.assetTypeHex = self.asset_type_hex
        self.parameterTypeHex = self.parameter_type_hex
        self.parameterName = self.parameter_name
        self.stationId = self.station_id
        self.stationCode = self.station_code
        return self

    class Config:
        from_attributes = True


class AssetParameterListResponse(BaseModel):
    total:       int
    page:        int
    page_size:   int
    total_pages: int
    rows:        List[AssetParameterResponse]


# Forward refs
ZoneWithDivisions.model_rebuild()
DivisionWithStations.model_rebuild()
AlertFiltersResponse.model_rebuild()
# ─── Auth ─────────────────────────────────────────────────────────────────────

class UserLoginRequest(BaseModel):
    employee_id: str
    password: str
    remember_me: bool = False

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class LoginUserResponse(BaseModel):
    id: int
    employee_id: str
    fullName: str
    role: Optional[int] = None
    email: Optional[str] = None
    designation: Optional[str] = None
    zone_id: Optional[int] = None
    division_id: Optional[int] = None
    mobile_number: Optional[str] = None
    reporting_officer_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_code: Optional[str] = None
    division_name: Optional[str] = None
    division_code: Optional[str] = None
    role_name: Optional[str] = None
    role_display_name: Optional[str] = None

class LoginDataResponse(BaseModel):
    token: str
    refresh_token: Optional[str] = None
    user: LoginUserResponse

class LoginResponse(BaseModel):
    status: bool
    message: str
    data: LoginDataResponse

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class LogoutResponse(BaseModel):
    message: str

class UserResponse(BaseModel):
    id: int
    full_name: str
    employee_id: str
    designation: str
    role_id: Optional[int] = None
    zone_id: Optional[int] = None
    division_id: Optional[int] = None
    email: str
    mobile_number: str
    reporting_officer_id: Optional[int] = None
    is_active: bool
    created_at: datetime
    zone_name: Optional[str] = None
    zone_code: Optional[str] = None
    division_name: Optional[str] = None
    division_code: Optional[str] = None
    role_name: Optional[str] = None
    role_display_name: Optional[str] = None
    class Config:
        from_attributes = True
# ─── RBAC ─────────────────────────────────────────────────────────────────────

class MenuBase(BaseModel):
    name: str
    slug: str
    parent_slug: Optional[str] = None
    icon: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True

class MenuCreate(MenuBase):
    pass

class MenuUpdate(BaseModel):
    name: Optional[str] = None
    parent_slug: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

class MenuResponse(MenuBase):
    id: int
    path: str
    href: str
    roles: List[int] = []
    class Config:
        from_attributes = True


class MenuTreeResponse(BaseModel):
    id: int
    label: str
    icon: Optional[str] = None
    sort_order: int = 0
    roles: List[int] = []
    href: Optional[str] = None
    children: List["MenuTreeResponse"] = Field(default_factory=list)

    class Config:
        from_attributes = True


class RoleMenuAssign(BaseModel):
    menu_id: int = Field(validation_alias=AliasChoices('menu_id', 'id'))
    permission: str = "view"   # view / edit / full

class RoleMenuResponse(BaseModel):
    menu_id: int
    menu_name: str
    menu_slug: str
    parent_slug: Optional[str]
    permission: str
    class Config:
        from_attributes = True


class RoleBase(BaseModel):
    name: str
    display_name: str
    level: int = 0
    description: Optional[str] = None
    is_active: bool = True

class RoleCreate(RoleBase):
    menus: Optional[List[RoleMenuAssign]] = []   # assign menus at creation time

class RoleUpdate(BaseModel):
    display_name: Optional[str] = None
    level: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None

class RoleResponse(RoleBase):
    id: int
    created_at: datetime
    menus: List[MenuTreeResponse] = []
    class Config:
        from_attributes = True


# ─── User Management ──────────────────────────────────────────────────────────

class RoleMinimalResponse(BaseModel):
    id: int
    name: str
    display_name: str
    level: int

    class Config:
        from_attributes = True



class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    designation: Optional[str] = None
    role_id: Optional[int] = None
    zone_id: Optional[int] = None
    division_id: Optional[int] = None
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    reporting_officer_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    confirm_password: Optional[str] = None

class UserDetailResponse(BaseModel):
    id: int
    full_name: str
    employee_id: str
    designation: str
    role_id: Optional[int]
    role_name: Optional[str]
    role_display_name: Optional[str]
    zone_id: Optional[int]
    division_id: Optional[int]
    mobile_number: str
    email: str
    reporting_officer_id: Optional[int]
    is_active: bool
    created_at: datetime
    menus: List[RoleMenuResponse] = []   # menus this user can access via their role
    role: Optional[RoleMinimalResponse] = None
    zone: Optional[ZoneMinimalResponse] = None
    division: Optional[DivisionMinimalResponse] = None

    class Config:
        from_attributes = True

class UserListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[UserDetailResponse]

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: str

# Update UserRegisterRequest to include role_id
class UserRegisterRequest(BaseModel):
    full_name: str
    employee_id: str
    designation: str
    role_id: Optional[int] = None        # ← NEW
    zone_id: Optional[int] = None
    division_id: Optional[int] = None
    mobile_number: str
    email: str
    password: str
    confirm_password: str
    reporting_officer_id: Optional[int] = None


class EquipmentRoomResponse(BaseModel):
    id: int
    station_id: int
    zone_id: int
    zone_code: str
    zone_name: str
    division_id: int
    division_code: str
    division_name: str
    station_code: str
    station_name: str
    room_type: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    door_status: Optional[str] = "CLOSED"
    updated_at: datetime

    class Config:
        from_attributes = True


class EquipmentRoomHistoryRow(BaseModel):
    id: str
    zone_code: str
    division_code: str
    station_code: str
    station_name: str
    timestamp: datetime
    room_type: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    door_status: Optional[str] = None

    class Config:
        from_attributes = True


class EquipmentRoomHistoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[EquipmentRoomHistoryRow]


# ─── Maintenance Mode ─────────────────────────────────────────────────────────

class MaintenanceModeRequest(BaseModel):
    station_id: int
    asset_no: str
    from_time: Optional[datetime] = None
    to_time: Optional[datetime] = None
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None


class MaintenanceModeResponse(BaseModel):
    id: int
    zone_id: int
    zone_code: str
    zone_name: str
    division_id: int
    division_code: str
    division_name: str
    station_id: int
    station_code: str
    station_name: str
    asset_type_hex: str
    asset_type_name: str
    asset_no: str
    from_time: datetime
    to_time: datetime
    from_date: datetime
    to_date: datetime
    status: str
    is_cleared: bool
    cleared_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MaintenanceModeListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[MaintenanceModeResponse]


class AlertCategoryEnum(str, PyEnum):
    FAILURE = "FAILURE"
    PREDICTIVE = "PREDICTIVE"

class AlertCauseCreate(BaseModel):
    cause_code: str = Field(..., max_length=50, validation_alias=AliasChoices('cause_code', 'causeCode'))
    cause_detail: str = Field(..., validation_alias=AliasChoices('cause_detail', 'causeDetail'))
    asset_type_id: Optional[str] = Field(None, max_length=2, validation_alias=AliasChoices('asset_type_id', 'assetTypeId'))
    alert_category: AlertCategoryEnum = Field(..., validation_alias=AliasChoices('alert_category', 'alertCategory'))

class AlertCauseUpdate(BaseModel):
    cause_detail: Optional[str] = Field(None, validation_alias=AliasChoices('cause_detail', 'causeDetail'))
    asset_type_id: Optional[str] = Field(None, max_length=2, validation_alias=AliasChoices('asset_type_id', 'assetTypeId'))
    alert_category: Optional[AlertCategoryEnum] = Field(None, validation_alias=AliasChoices('alert_category', 'alertCategory'))

class AlertCauseResponse(BaseModel):
    cause_code: str
    cause_detail: str
    asset_type_id: Optional[str] = None
    alert_category: AlertCategoryEnum
    created_at: datetime
    asset_type_name: Optional[str] = None

    # CamelCase aliases for frontend compatibility
    causeCode: str = ""
    causeDetail: str = ""
    assetTypeId: Optional[str] = None
    alertCategory: str = ""
    assetTypeName: Optional[str] = None

    @model_validator(mode="after")
    def populate_aliases(self) -> "AlertCauseResponse":
        self.causeCode = self.cause_code
        self.causeDetail = self.cause_detail
        self.assetTypeId = self.asset_type_id
        self.alertCategory = self.alert_category.value
        self.assetTypeName = self.asset_type_name
        return self

    class Config:
        from_attributes = True

class AlertCauseListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[AlertCauseResponse]


# ─── Slave Card ─────────────────────────────────────────────────────────────

class SlaveCardBase(BaseModel):
    gateway_id: int = Field(..., validation_alias=AliasChoices('gateway_id', 'gatewayId'))
    card_address: str = Field(..., pattern=r"^[0-9A-Fa-f]{1,2}$", validation_alias=AliasChoices('card_address', 'cardAddress'), description="1-byte hex card address, e.g. '81'")
    card_type: Optional[str] = Field(None, max_length=20, validation_alias=AliasChoices('card_type', 'cardType'), description="e.g. 'Voltage', 'Analog', 'DI'")

    @field_validator("card_address", mode="before")
    @classmethod
    def normalize_card_address(cls, v: Any) -> str:
        if v is None:
            return v
        if isinstance(v, int):
            v = str(v)
        v_str = str(v).strip().upper()
        if v_str.startswith("0X"):
            v_str = v_str[2:]
        return v_str

class SlaveCardCreate(SlaveCardBase):
    pass

class SlaveCardUpdate(BaseModel):
    gateway_id: Optional[int] = Field(None, validation_alias=AliasChoices('gateway_id', 'gatewayId'))
    card_address: Optional[str] = Field(None, pattern=r"^[0-9A-Fa-f]{1,2}$", validation_alias=AliasChoices('card_address', 'cardAddress'))
    card_type: Optional[str] = Field(None, max_length=20, validation_alias=AliasChoices('card_type', 'cardType'))

    @field_validator("card_address", mode="before")
    @classmethod
    def normalize_card_address(cls, v: Any) -> Optional[str]:
        if v is None:
            return v
        if isinstance(v, int):
            v = str(v)
        v_str = str(v).strip().upper()
        if v_str.startswith("0X"):
            v_str = v_str[2:]
        return v_str

class SlaveCardResponse(SlaveCardBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    gatewayId: int = 0
    cardAddress: str = ""
    cardType: Optional[str] = None

    stngw_id: Optional[str] = None
    stngwId: Optional[str] = None
    station_id: Optional[int] = None
    stationId: Optional[int] = None
    station_name: Optional[str] = None
    stationName: Optional[str] = None

    @model_validator(mode="after")
    def populate_aliases(self) -> "SlaveCardResponse":
        self.gatewayId = self.gateway_id
        self.cardAddress = self.card_address
        self.cardType = self.card_type
        self.stngwId = self.stngw_id
        self.stationId = self.station_id
        self.stationName = self.station_name
        if not self.updated_at:
            self.updated_at = self.created_at
        return self

    class Config:
        from_attributes = True


class SlaveCardListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[SlaveCardResponse]


class SystemHealthItem(BaseModel):
    total: int
    faulty: int


class SystemHealthTotalsResponse(BaseModel):
    sensors: SystemHealthItem
    iot_devices: SystemHealthItem
    network: SystemHealthItem
    station_gateway: SystemHealthItem


class FaultyByStationItem(BaseModel):
    station_code: str
    asset_code: str
    asset_type: Optional[str] = None
    sensor_faulty: int
    iot_faulty: int
    net_faulty: int
    gw_faulty: int


class FaultyByStationResponse(BaseModel):
    total: int
    total_records: Optional[int] = None
    rows: List[FaultyByStationItem]
    page: Optional[int] = 1
    page_size: Optional[int] = 10
    total_pages: Optional[int] = 1


class LiveParameterItem(BaseModel):
    param: str
    value: str
    range: str
    trend: str


class ThrowTimeCyclePoint(BaseModel):
    cycle: int
    seconds: float


class TelemetryLiveCardResponse(BaseModel):
    station_code: str
    asset_number: str
    asset_type_name: str
    last_sync: str
    asset_status: str
    parameters: List[LiveParameterItem]
    throw_time_cycles: List[ThrowTimeCyclePoint]
    threshold_seconds: float


class StationPerformanceItem(BaseModel):
    station_code: str
    station_name: str
    failure_accuracy: float
    predictive_accuracy: float
    actual_detection_rate: float


class PerformanceOverviewResponse(BaseModel):
    confirmed_failure_percentage: float
    confirmed_predictive_percentage: float
    actual_failures_caught_percentage: float
    by_station: List[StationPerformanceItem]


class AssetCategorySummaryItem(BaseModel):
    category_key: str
    category_name: str
    normal_count: int
    failed_count: int
    predicted_count: int


class LiveAlertShortcuts(BaseModel):
    alert_history_count: int
    alert_live_count: int


class MobileDashboardSummaryResponse(BaseModel):
    zone_code: str
    division_code: str
    station_code: str
    live_alerts: LiveAlertShortcuts
    assets_by_category: List[AssetCategorySummaryItem]
    fleet_health: FleetHealthSummary
    infrastructure: InfrastructureSummary


class FleetHealthSummary(BaseModel):
    normal_percentage: float
    normal_count: int
    predicted_count: int
    failed_count: int


class InfrastructureSummary(BaseModel):
    sensors_ok: int
    sensors_flt: int
    iot_ok: int
    iot_flt: int


T = TypeVar("T")

class StandardResponse(BaseModel, Generic[T]):
    status: bool = True
    message: str = "Success"
    data: T

    @model_validator(mode="before")
    @classmethod
    def force_success_message(cls, data):
        if isinstance(data, dict):
            if data.get("status") is not False:
                data["message"] = "Success"
        return data


# ─── Channel Assignment ───────────────────────────────────────────────────────

class ChannelAssignmentCreate(BaseModel):
    gateway_id: Optional[int] = Field(None, validation_alias=AliasChoices('gateway_id', 'gatewayId'))
    slave_card_id: int = Field(..., validation_alias=AliasChoices('slave_card_id', 'slaveCardId'))
    channel_number: str = Field(..., max_length=15, validation_alias=AliasChoices('channel_number', 'channelNumber', 'channel'))
    asset_id: int = Field(..., validation_alias=AliasChoices('asset_id', 'assetId'))
    para_id: Optional[str] = Field(None, pattern=r"^[0-9A-Fa-f]{7,8}$", validation_alias=AliasChoices('para_id', 'paraId'))
    prloc: Optional[str] = Field(None, max_length=50, validation_alias=AliasChoices('prloc', 'location_box', 'locationBox'))


class ChannelAssignmentUpdate(BaseModel):
    gateway_id: Optional[int] = Field(None, validation_alias=AliasChoices('gateway_id', 'gatewayId'))
    slave_card_id: Optional[int] = Field(None, validation_alias=AliasChoices('slave_card_id', 'slaveCardId'))
    asset_id: Optional[int] = Field(None, validation_alias=AliasChoices('asset_id', 'assetId'))
    channel_number: Optional[str] = Field(None, max_length=15, validation_alias=AliasChoices('channel_number', 'channelNumber', 'channel'))
    para_id: Optional[str] = Field(None, pattern=r"^[0-9A-Fa-f]{7,8}$", validation_alias=AliasChoices('para_id', 'paraId'))
    prloc: Optional[str] = Field(None, max_length=50, validation_alias=AliasChoices('prloc', 'location_box', 'locationBox'))
    is_assigned: Optional[bool] = Field(None, validation_alias=AliasChoices('is_assigned', 'isAssigned'))


class ChannelAssignmentResponse(BaseModel):
    id: int
    zone_id: Optional[int] = None
    zone_code: Optional[str] = None
    zone_name: Optional[str] = None
    division_id: Optional[int] = None
    division_code: Optional[str] = None
    division_name: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None
    station_name: Optional[str] = None
    gateway_id: Optional[int] = None
    stngw_id: Optional[str] = None
    slave_card_id: Optional[int] = None
    card_number: Optional[str] = None
    card_address: Optional[str] = None
    card_type: Optional[str] = None
    channel: Optional[str] = None
    channel_number: Optional[str] = None
    para_id: str
    asset_id: Optional[int] = None
    asset_number_code: Optional[str] = None
    asset_name: Optional[str] = None
    prloc: Optional[str] = None
    location_box: Optional[str] = None
    is_assigned: bool = True
    created_at: datetime
    updated_at: datetime

    # CamelCase aliases for frontend
    gatewayId: Optional[int] = None
    stngwId: Optional[str] = None
    slaveCardId: Optional[int] = None
    cardNumber: Optional[str] = None
    cardAddress: Optional[str] = None
    cardType: Optional[str] = None
    channelNumber: Optional[str] = None
    paraId: Optional[str] = None
    assetId: Optional[int] = None
    assetNumberCode: Optional[str] = None
    assetName: Optional[str] = None
    locationBox: Optional[str] = None
    isAssigned: bool = True
    updatedAt: Optional[str] = None

    @model_validator(mode="after")
    def populate_aliases(self) -> "ChannelAssignmentResponse":
        self.gatewayId = self.gateway_id
        self.stngwId = self.stngw_id
        self.slaveCardId = self.slave_card_id
        self.cardNumber = self.card_number
        self.cardAddress = self.card_address
        self.cardType = self.card_type
        self.channelNumber = self.channel_number or self.channel
        self.channel = self.channel or self.channel_number
        self.paraId = self.para_id
        self.assetId = self.asset_id
        self.assetNumberCode = self.asset_number_code
        self.assetName = self.asset_name
        self.locationBox = self.prloc
        self.isAssigned = self.is_assigned
        self.updatedAt = self.updated_at.isoformat() if self.updated_at else None
        return self

    class Config:
        from_attributes = True


class ChannelAssignmentListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    rows: List[ChannelAssignmentResponse]


class AvailableChannelItem(BaseModel):
    channel: str
    channel_number: str
    channel_index: int
    is_assigned: bool
    current_para_id: Optional[str] = None
    current_asset_id: Optional[int] = None
    current_asset_number_code: Optional[str] = None
    current_prloc: Optional[str] = None


class AvailableChannelsResponse(BaseModel):
    slave_card_id: int
    card_address: str
    card_type: Optional[str] = None
    total_channels: int
    assigned_count: int
    available_count: int
    channels: List[AvailableChannelItem]










