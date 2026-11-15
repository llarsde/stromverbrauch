import json

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "strom.db")
    monkeypatch.setattr(db, "PHOTO_DIR", tmp_path / "photos")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


SEED = {
    "tariffs": [{"valid_from": "2025-10-09", "energy_ct": 30.61387, "tax_ct": 2.05, "base_eur_year": 143.01, "vat_pct": 19}],
    "periods": [{"start": "2026-10-09", "end": "2027-10-08", "start_value": 7229}],
    "installments": [{"valid_from": "2026-11-01", "amount": 125, "due_day": 8}],
    "readings": [
        {"ts": "2026-10-29T12:00:00+01:00", "value": 7755, "kind": "ablesung"},
        {"ts": "2026-11-15T12:00:00+01:00", "value": 7918, "kind": "ablesung"},
    ],
}


def test_ablauf(client):
    r = client.post("/api/import", files={"file": ("d.json", json.dumps(SEED), "application/json")})
    assert r.json()["imported"]["readings"] == 2

    # unplausibler Stand wird erst nach Bestätigung gespeichert
    r = client.post("/api/readings", data={"value": "7000", "ts": "2026-11-16T08:00"})
    assert r.status_code == 409
    r = client.post("/api/readings", data={"value": "7930", "ts": "2026-11-16T08:00"},
                    files={"photo": ("z.jpg", b"img", "image/jpeg")})
    assert r.status_code == 200
    rows = client.get("/api/readings").json()
    assert rows[0]["value"] == 7930 and rows[0]["photo"]
    assert client.get(f"/api/photos/{rows[0]['photo']}").content == b"img"
    assert client.get("/api/photos/..%2Fstrom.db").status_code == 404

    s = client.get("/api/summary").json()
    assert s["period"]["installments_total"] == 1500
    assert client.get("/").status_code == 200

    exp = client.get("/api/export").json()
    assert len(exp["readings"]) == 3
    client.delete(f"/api/readings/{rows[0]['id']}")
    assert len(client.get("/api/readings").json()) == 2
