import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.models import Gateway, SlaveCard, Asset, Station, Division, Zone, AssetParameter

client = TestClient(app)


def get_auth_token():
    res = client.post("/auth/login", json={"employee_id": "hq_admin", "password": "admin123", "remember_me": False})
    assert res.status_code == 200, f"Login failed: {res.text}"
    return res.json()["data"]["token"]


@pytest.fixture(scope="module")
def auth_headers():
    token = get_auth_token()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def setup_test_hardware():
    db = SessionLocal()
    # Find or use existing station
    station = db.query(Station).first()
    assert station is not None, "Station must exist"

    # Gateway
    gw = db.query(Gateway).filter(Gateway.station_id == station.id).first()
    if not gw:
        gw = Gateway(stngw_id="77889900", station_id=station.id, imei="86795007928753")
        db.add(gw)
        db.commit()
        db.refresh(gw)

    # Slave Card 1 (Voltage)
    card_voltage = db.query(SlaveCard).filter(SlaveCard.gateway_id == gw.id, SlaveCard.card_address == "91").first()
    if not card_voltage:
        card_voltage = SlaveCard(gateway_id=gw.id, card_address="91", card_type="Voltage")
        db.add(card_voltage)
        db.commit()
        db.refresh(card_voltage)

    # Slave Card 2 (DI)
    card_di = db.query(SlaveCard).filter(SlaveCard.gateway_id == gw.id, SlaveCard.card_address == "92").first()
    if not card_di:
        card_di = SlaveCard(gateway_id=gw.id, card_address="92", card_type="DI")
        db.add(card_di)
        db.commit()
        db.refresh(card_di)

    # Asset
    asset = db.query(Asset).filter(Asset.station_id == station.id).first()
    if not asset:
        asset = Asset(
            smms_asset_code="TEST_PT_99",
            smms_asset_name="Test Point 99",
            asset_number_code="101T",
            asset_number_id="01",
            asset_type_hex="00",
            station_gateway_id=gw.stngw_id,
            station_id=station.id,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)

    hw_info = {
        "station_id": station.id,
        "gateway_id": gw.id,
        "stngw_id": gw.stngw_id,
        "card_voltage_id": card_voltage.id,
        "card_di_id": card_di.id,
        "asset_id": asset.id,
        "asset_code": asset.asset_number_code,
    }
    db.close()
    return hw_info


def test_list_channel_assignments(auth_headers):
    res = client.get("/channel-assignments/", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] is True
    assert "data" in data
    assert "rows" in data["data"]
    assert "total" in data["data"]


def test_available_channels_dynamic_capacity(auth_headers, setup_test_hardware):
    hw = setup_test_hardware

    # 1. Voltage card capacity = 12
    res_v = client.get(f"/channel-assignments/available-channels?slave_card_id={hw['card_voltage_id']}", headers=auth_headers)
    assert res_v.status_code == 200
    data_v = res_v.json()["data"]
    assert data_v["total_channels"] >= 12
    assert any(c["channel"] == "CH1" for c in data_v["channels"])

    # 2. DI card capacity = 16
    res_di = client.get(f"/channel-assignments/available-channels?slave_card_id={hw['card_di_id']}", headers=auth_headers)
    assert res_di.status_code == 200
    data_di = res_di.json()["data"]
    assert data_di["total_channels"] >= 16
    assert any(c["channel"] == "CH16" for c in data_di["channels"])


def test_create_and_reuse_channel_assignment(auth_headers, setup_test_hardware):
    hw = setup_test_hardware

    # 1. Create assignment with 7-character hex para_id (testing leading zero & 7-char preservation)
    payload = {
        "gateway_id": hw["gateway_id"],
        "slave_card_id": hw["card_voltage_id"],
        "channel_number": "CH1",
        "asset_id": hw["asset_id"],
        "para_id": "000101F",  # 7-char hex from spreadsheet
        "prloc": "LB-01",
    }
    res = client.post("/channel-assignments/", json=payload, headers=auth_headers)
    assert res.status_code == 201, f"Failed create: {res.text}"
    created = res.json()["data"]
    assert created["para_id"] == "000101F"
    assert created["channel_number"] == "CH1"
    assert created["card_address"] == "91"
    assert created["location_box"] == "LB-01"
    assignment_id = created["id"]

    # 2. Attempt duplicate assignment on the same channel -> expect 409 Conflict
    dup_res = client.post("/channel-assignments/", json=payload, headers=auth_headers)
    assert dup_res.status_code == 409, f"Expected 409 on duplicate, got {dup_res.status_code}"

    # 3. Update assignment (e.g. change location box)
    update_res = client.put(
        f"/channel-assignments/{assignment_id}",
        json={"prloc": "LB-02"},
        headers=auth_headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["data"]["location_box"] == "LB-02"

    # 4. Delete / unassign assignment
    del_res = client.delete(f"/channel-assignments/{assignment_id}", headers=auth_headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] is True

    # 5. Channel should now be available again, and re-assigning should reuse the row!
    reassign_payload = {
        "gateway_id": hw["gateway_id"],
        "slave_card_id": hw["card_voltage_id"],
        "channel_number": "CH1",
        "asset_id": hw["asset_id"],
        "para_id": "000101F",
        "prloc": "LB-03",
    }
    reassign_res = client.post("/channel-assignments/", json=reassign_payload, headers=auth_headers)
    assert reassign_res.status_code == 201
    reassigned = reassign_res.json()["data"]
    assert reassigned["id"] == assignment_id  # Reused same record!
    assert reassigned["location_box"] == "LB-03"

    # Clean up
    client.delete(f"/channel-assignments/{assignment_id}", headers=auth_headers)


def test_filter_assignments_cascading(auth_headers, setup_test_hardware):
    hw = setup_test_hardware
    # Test filter by station_id
    res = client.get(f"/channel-assignments/?station_id={hw['station_id']}", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] is True

    # Test filter-options endpoint
    opts_res = client.get(f"/channel-assignments/filter-options?station_id={hw['station_id']}", headers=auth_headers)
    assert opts_res.status_code == 200
    opts = opts_res.json()["data"]
    assert "zones" in opts
    assert "divisions" in opts
    assert "stations" in opts
    assert "gateways" in opts
    assert "slave_cards" in opts


def test_invalid_asset_station_mismatch(auth_headers, setup_test_hardware):
    hw = setup_test_hardware
    db = SessionLocal()
    # Find asset in a different station
    other_asset = db.query(Asset).filter(Asset.station_id != hw["station_id"]).first()
    db.close()
    if not other_asset:
        pytest.skip("No asset from different station to test mismatch")

    payload = {
        "gateway_id": hw["gateway_id"],
        "slave_card_id": hw["card_voltage_id"],
        "channel_number": "CH2",
        "asset_id": other_asset.id,
        "prloc": "LB-01",
    }
    res = client.post("/channel-assignments/", json=payload, headers=auth_headers)
    assert res.status_code == 400
    assert "station" in res.text.lower()
