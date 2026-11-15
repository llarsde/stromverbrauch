from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import calc, db, ha, mqtt

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=TZ)


def test_zaehler_mit_ruecksetzer():
    s = [(NOW - timedelta(days=8), 10.0), (NOW - timedelta(days=6), 12.0),
         (NOW - timedelta(days=3), 0.5),  # Steckdose zurückgesetzt
         (NOW - timedelta(days=1), 2.0)]
    r = calc.counter_consumption(s, NOW - timedelta(days=7), NOW)
    assert r["kwh"] == pytest.approx(2.0 + 0.5 + 1.5)
    assert r["days"] == pytest.approx(6.0)


def test_verbraucher_anteil():
    s = [(NOW - timedelta(days=7) + timedelta(hours=h), h * 0.05) for h in range(0, 24 * 7 + 1)]
    st = calc.consumer_stats([{"name": "Waschmaschine", "samples": s, "power": 3.2}], 10.0, NOW)
    item = st["items"][0]
    assert item["per_day"] == pytest.approx(1.2, rel=1e-3)
    assert item["share"] == pytest.approx(12, rel=1e-3)
    assert st["rest"]["per_day"] == pytest.approx(8.8, rel=1e-3)


def test_verbraucher_ohne_daten():
    st = calc.consumer_stats([{"name": "PC", "samples": [], "power": None}], 10.0, NOW)
    assert st["items"][0]["kwh"] is None and st["rest"]["per_day"] == 10.0


def test_wert_aus_zustand():
    assert ha.value_of({"state": "12.5"}, "verbraucher") == 12.5
    assert ha.value_of({"state": "unavailable"}, "verbraucher") is None
    assert ha.value_of({"state": "cloudy", "attributes": {"temperature": 7.3}}, "wetter") == 7.3


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "strom.db")
    monkeypatch.setattr(db, "PHOTO_DIR", tmp_path / "photos")
    db.init()


def fake_ha(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/states/sensor.wm_energie":
        return httpx.Response(200, json={"state": "5.25", "attributes": {}})
    if path == "/api/states/sensor.wm_leistung":
        return httpx.Response(200, json={"state": "2.1", "attributes": {}})
    if path == "/api/states/weather.forecast_home":
        return httpx.Response(200, json={"state": "rainy", "attributes": {"temperature": 6.5}})
    if path.startswith("/api/history/period/"):
        eid = request.url.params["filter_entity_id"]
        if eid == "sensor.wm_energie":
            return httpx.Response(200, json=[[
                {"state": "4.0", "last_changed": "2026-11-10T08:00:00+00:00"},
                {"state": "unavailable", "last_changed": "2026-11-11T08:00:00+00:00"},
                {"state": "5.0", "last_changed": "2026-11-12T08:00:00+00:00"},
            ]])
        return httpx.Response(200, json=[[
            {"state": "sunny", "attributes": {"temperature": 4.0}, "last_changed": "2026-11-12T08:00:00+00:00"}]])
    return httpx.Response(404)


def test_abfrage_und_verlauf(tmpdb):
    db.insert("ha_entities", {"role": "verbraucher", "name": "Waschmaschine",
                              "entity_id": "sensor.wm_energie", "power_entity_id": "sensor.wm_leistung"})
    db.insert("ha_entities", {"role": "wetter", "name": "Wetter", "entity_id": "weather.forecast_home"})
    client = ha.HAClient("http://ha.local", "t", transport=httpx.MockTransport(fake_ha))
    res = ha.poll(client)
    assert res == {"stored": 2, "missing": []}
    assert ha.current_power["sensor.wm_leistung"] == 2.1
    rows = db.rows("SELECT * FROM samples WHERE entity_id = 'sensor.wm_energie' ORDER BY ts")
    assert [r["value"] for r in rows] == [4.0, 5.0, 5.25]   # Verlauf + aktueller Wert
    assert rows[0]["ts"] == "2026-11-10T09:00:00+01:00"     # in lokale Zeit umgerechnet
    # Verlauf wird nur einmal übernommen
    ha.poll(client)
    assert db.rows("SELECT COUNT(*) n FROM samples WHERE entity_id='sensor.wm_energie'")[0]["n"] >= 3
    assert db.kv_get("backfilled:sensor.wm_energie")


def test_mqtt_werte():
    s = {"last_reading": {"ts": "2026-11-15T12:00:00+01:00", "value": 7918.0},
         "estimated_now": 7920.04, "rate": {"recent": 9.588}, "day": {"eur": 4.19},
         "month": {"kwh_projected": 287.6, "eur_projected": 125.8}, "year": {"kwh": 3499.7},
         "period": {"kwh_projected": 3828.7, "eur_projected": 1658.4, "balance": -158.42,
                    "recommended_installment": 140, "break_even_rate": 8.3436}}
    out = mqtt.state_from_summary(s, datetime(2026, 11, 16, 12, 0, tzinfo=TZ))
    assert out["zaehlerstand"] == 7918.0
    assert out["tage_seit_ablesung"] == 1.0
    assert out["saldo"] == -158.42
    assert out["break_even"] == 8.34
    assert "abweichung" not in out
    keys = {k for k, *_ in mqtt.SENSORS}
    assert set(out) <= keys


def test_fehlertexte():
    req = httpx.Request("GET", "https://ha.example.org/api/")
    cf = httpx.HTTPStatusError("x", request=req, response=httpx.Response(403, headers={"server": "cloudflare"}, request=req))
    assert "Cloudflare" in ha.explain_error(cf)
    ban = httpx.HTTPStatusError("x", request=req, response=httpx.Response(403, request=req))
    assert "gesperrt" in ha.explain_error(ban)
    tok = httpx.HTTPStatusError("x", request=req, response=httpx.Response(401, request=req))
    assert "Token" in ha.explain_error(tok)
