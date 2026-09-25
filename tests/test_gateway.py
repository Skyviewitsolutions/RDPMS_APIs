import pytest
from datetime import datetime, UTC
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, get_db
from app.auth_utils import get_current_user
from app.models.models import Gateway, Station, Division, Zone, User, Role, SlaveCard, Asset
from app.routers.gateway import _decode_and_resolve_hierarchy, _resolve_station_from_stngw_id


from sqlalchemy.pool import StaticPool

# Setup isolated in-memory SQLite database
TEST_DB_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="module", autouse=True)
def init_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    # Seed Role & Admin User
    role = Role(id=1, name="Admin", display_name="Administrator", level=0, is_active=True)
    db.add(role)
    db.commit()

    user = User(
        id=1,
        full_name="HQ Admin",
        employee_id="hq_admin",
        designation="Admin",
        mobile_number="9999999999",
        email="admin@rdpms.in",
        hashed_password="hashed_dummy_password",
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    db.commit()

    # Seed Hierarchy: Zone (01) -> Division (02) -> Station (03)
    zone = Zone(
        id=1,
        zone_name="Northern Railway",
        zone_code="NR",
        zone_id_hex="01",
        status="Active",
    )
    db.add(zone)
    db.commit()

    division = Division(
        id=1,
        division_name="Delhi Division",
        division_code="DLI",
        division_id_hex="02",
        zone_id=zone.id,
        status="Active",
    )
    db.add(division)
    db.commit()

    station = Station(
        id=1,
        station_name="New Delhi",
        station_code="NDLS",
        station_id_hex="03",
        division_id=division.id,
        status="Active",
    )
    db.add(station)

    # Seed a second station for conflict testing
    station2 = Station(
        id=2,
        station_name="Old Delhi",
        station_code="DLI-STN",
        station_id_hex="04",
        division_id=division.id,
        status="Active",
    )
    db.add(station2)
    db.commit()

    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def override_get_current_user():
    db = TestingSessionLocal()
    user = (
        db.query(User)
        .options(joinedload(User.role))
        .filter(User.employee_id == "hq_admin")
        .first()
    )
    # Expunge so it can be accessed in test endpoint handlers without lazy load issues
    db.expunge_all()
    db.close()
    return user


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user
client = TestClient(app)


def test_decode_and_resolve_hierarchy_invalid_format():
    db = TestingSessionLocal()
    # Less than 8 chars
    res = _decode_and_resolve_hierarchy("12345", db)
    assert not res["is_valid"]
    assert not res["can_register"]
    assert "8 hexadecimal characters" in res["error"]

    # Non-hex characters
    res = _decode_and_resolve_hierarchy("0102ZZ00", db)
    assert not res["is_valid"]
    assert not res["can_register"]
    db.close()


def test_decode_and_resolve_hierarchy_unregistered_zone():
    db = TestingSessionLocal()
    res = _decode_and_resolve_hierarchy("FE020301", db)
    assert not res["is_valid"]
    assert not res["can_register"]
    assert "Zone with hex code 'FE' does not exist" in res["error"]
    db.close()


def test_decode_and_resolve_hierarchy_unregistered_division():
    db = TestingSessionLocal()
    # Zone 01 exists, but Division 99 does not
    res = _decode_and_resolve_hierarchy("01990301", db)
    assert not res["is_valid"]
    assert not res["can_register"]
    assert "Division with hex code '99' does not exist under Zone 'NR'" in res["error"]
    db.close()


def test_decode_and_resolve_hierarchy_unregistered_station():
    db = TestingSessionLocal()
    # Zone 01 and Division 02 exist, but Station 99 does not
    res = _decode_and_resolve_hierarchy("01029901", db)
    assert not res["is_valid"]
    assert not res["can_register"]
    assert "Station with hex code '99' does not exist under Division 'DLI'" in res["error"]
    db.close()


def test_decode_and_resolve_hierarchy_valid():
    db = TestingSessionLocal()
    # Zone 01 + Division 02 + Station 03 + Gateway 0A (10)
    stngw_id = "0102030A"
    res = _decode_and_resolve_hierarchy(stngw_id, db)
    assert res["is_valid"] is True
    assert res["can_register"] is True
    assert res["resolved_station_id"] == 1
    assert res["gateway_number"] == 10
    assert res["gateway_number_hex"] == "0A"
    assert res["zone"]["code"] == "NR"
    assert res["division"]["code"] == "DLI"
    assert res["station"]["code"] == "NDLS"

    station_id = _resolve_station_from_stngw_id(stngw_id, db)
    assert station_id == 1
    db.close()


def test_preview_endpoint():
    # Valid hierarchy preview
    res = client.get("/gateway/preview/01020305")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] is True
    assert body["data"]["gateway_number"] == 5
    assert body["data"]["station"]["code"] == "NDLS"
    assert body["data"]["can_register"] is True

    # Invalid hierarchy preview
    res_bad = client.get("/gateway/preview/99887766")
    assert res_bad.status_code == 200
    body_bad = res_bad.json()
    assert body_bad["status"] is False
    assert body_bad["data"]["can_register"] is False
    assert "does not exist" in body_bad["data"]["error"]


def test_create_gateway_strict_registration():
    db = TestingSessionLocal()

    # 1. Unregistered hierarchy fails with HTTP 400
    bad_res = client.post("/gateway/", json={"stngw_id": "FF998801"})
    assert bad_res.status_code == 400
    msg = bad_res.json().get("message") or bad_res.json().get("detail")
    assert "Cannot register gateway" in msg

    # 2. Valid hierarchy registration succeeds without manual station_id
    test_gw_id = "01020301"
    good_res = client.post(
        "/gateway/",
        json={"stngw_id": test_gw_id, "imei": "86749070579912"}
    )
    assert good_res.status_code == 201
    data = good_res.json()["data"]
    assert data["stngw_id"] == test_gw_id
    assert data["station_id"] == 1
    assert data["gateway_number"] == 1
    assert data["station_code"] == "NDLS"
    assert data["division_code"] == "DLI"
    assert data["zone_code"] == "NR"
    assert data["status"] == "Linked"

    # 3. Duplicate stngw_id fails with HTTP 400
    dup_res = client.post("/gateway/", json={"stngw_id": test_gw_id})
    assert dup_res.status_code == 400
    msg = dup_res.json().get("message") or dup_res.json().get("detail")
    assert "already exists" in msg

    # 4. Duplicate IMEI fails with HTTP 400
    dup_imei = client.post("/gateway/", json={"stngw_id": "01020302", "imei": "86749070579912"})
    assert dup_imei.status_code == 400
    msg = dup_imei.json().get("message") or dup_imei.json().get("detail")
    assert "already exists" in msg

    # 5. Invalid IMEI format fails
    bad_imei = client.post("/gateway/", json={"stngw_id": "01020302", "imei": "INVALID_IMEI_XYZ"})
    assert bad_imei.status_code == 422

    # 6. Provided conflicting station_id fails
    conflict_stn = client.post("/gateway/", json={"stngw_id": "01020302", "station_id": 999})
    assert conflict_stn.status_code == 400
    msg = conflict_stn.json().get("message") or conflict_stn.json().get("detail")
    assert "conflicts with the decoded station hierarchy" in msg

    db.close()


def test_link_station_enforces_hierarchy():
    db = TestingSessionLocal()
    # Create an unlinked gateway
    test_gw_id = "01020303"
    gw = Gateway(stngw_id=test_gw_id, station_id=None)
    db.add(gw)
    db.commit()

    # Attempt to link to conflicting station (ID 2 = DLI-STN, but hex 03 encodes NDLS = ID 1)
    bad_link = client.post(
        f"/gateway/{test_gw_id}/link-station",
        json={"station_id": 2}
    )
    assert bad_link.status_code == 400
    msg = bad_link.json().get("message") or bad_link.json().get("detail")
    assert "Cannot link gateway" in msg

    # Auto-link to authoritative decoded station
    good_link = client.post(f"/gateway/{test_gw_id}/link-station")
    assert good_link.status_code == 200
    assert good_link.json()["data"]["station_id"] == 1
    assert good_link.json()["data"]["gateway_number"] == 3

    db.close()


def test_list_gateways_returns_hierarchy():
    res = client.get("/gateway/list")
    assert res.status_code == 200
    rows = res.json()["data"]["rows"]
    assert len(rows) > 0
    first = rows[0]
    assert "gateway_number" in first
    assert "zone_code" in first
    assert "division_code" in first
    assert "station_code" in first


def test_update_gateway_imei_and_link():
    db = TestingSessionLocal()
    # Create unlinked gateway
    test_gw_id = "0102030B"
    gw = Gateway(stngw_id=test_gw_id, station_id=None, imei=None)
    db.add(gw)
    db.commit()

    # Link station AND set new IMEI
    new_imei = "86749070579933"
    res = client.post(
        f"/gateway/{test_gw_id}/link-station",
        json={"imei": new_imei}
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["stngw_id"] == test_gw_id
    assert data["station_id"] == 1
    assert data["imei"] == new_imei
    assert res.json()["status"] is True

    # Attempt to use duplicate IMEI on another gateway
    other_gw_id = "0102030C"
    other_gw = Gateway(stngw_id=other_gw_id, station_id=None, imei=None)
    db.add(other_gw)
    db.commit()

    dup_res = client.post(
        f"/gateway/{other_gw_id}/link-station",
        json={"imei": new_imei}
    )
    assert dup_res.status_code == 400
    msg = dup_res.json().get("message") or dup_res.json().get("detail")
    assert "already exists" in msg

    # Invalid IMEI format (less than 10 digits)
    bad_res = client.post(
        f"/gateway/{other_gw_id}/link-station",
        json={"imei": "12345"}
    )
    assert bad_res.status_code == 422

    db.close()


def test_put_update_gateway():
    # Update gateway using PUT /gateway/{stngw_id}
    test_gw_id = "0102030B"
    updated_imei = "86749070579944"
    res = client.put(
        f"/gateway/{test_gw_id}",
        json={"imei": updated_imei}
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["stngw_id"] == test_gw_id
    assert data["imei"] == updated_imei


def test_delete_gateway_success_and_protection():
    db = TestingSessionLocal()
    # 1. Test clean deletion
    clean_gw_id = "0102030D"
    gw1 = Gateway(stngw_id=clean_gw_id, station_id=1)
    db.add(gw1)
    db.commit()

    del_res = client.delete(f"/gateway/{clean_gw_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] is True
    # Ensure it no longer exists
    assert db.query(Gateway).filter(Gateway.stngw_id == clean_gw_id).first() is None

    # 2. Test deletion blocked when slave card is attached
    protected_gw_id = "0102030E"
    gw2 = Gateway(stngw_id=protected_gw_id, station_id=1)
    db.add(gw2)
    db.commit()
    db.refresh(gw2)

    sc = SlaveCard(gateway_id=gw2.id, card_address="91", card_type="Voltage")
    db.add(sc)
    db.commit()

    blocked_res = client.delete(f"/gateway/{protected_gw_id}")
    assert blocked_res.status_code == 400
    msg = blocked_res.json().get("message") or blocked_res.json().get("detail")
    assert "slave card(s)" in msg

    # 3. Test 404 for non-existent gateway
    not_found = client.delete("/gateway/99999999")
    assert not_found.status_code == 404

    db.close()
