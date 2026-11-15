"""Stromverbrauch – Web-API und Auslieferung der Web-App."""
from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import calc, db, ha, mqtt

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
STATIC = Path(__file__).parent / "static"
VERSION = os.environ.get("APP_VERSION", "dev")

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("stromverbrauch")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))

publisher: Optional[mqtt.Publisher] = None
_stop = threading.Event()
_wake = threading.Event()


def publish_now() -> None:
    """Aktuelle Werte sofort an MQTT senden (z. B. nach einer neuen Ablesung)."""
    if publisher:
        try:
            publisher.publish_state(mqtt.state_from_summary(build_summary(), datetime.now(TZ)))
        except Exception:  # noqa: BLE001 – Fehler beim Senden dürfen die API nie stören
            log.exception("MQTT-Senden fehlgeschlagen")


def worker() -> None:
    """Hintergrund: HA-Werte abfragen und Ergebnisse an MQTT senden."""
    client = ha.HAClient() if ha.configured() else None
    while not _stop.is_set():
        if client:
            try:
                res = ha.poll(client)
                msg = "ok" if not res["missing"] else f"nicht verfügbar: {', '.join(res['missing'])}"
                db.kv_set("ha_status", json.dumps({"ok": True, "message": msg,
                                                    "at": datetime.now(TZ).isoformat(timespec="seconds")}))
            except Exception as e:  # noqa: BLE001
                log.warning("Home Assistant nicht erreichbar: %s", e)
                db.kv_set("ha_status", json.dumps({"ok": False, "message": str(e)[:300],
                                                    "at": datetime.now(TZ).isoformat(timespec="seconds")}))
        publish_now()
        _wake.wait(POLL_SECONDS)
        _wake.clear()


@asynccontextmanager
async def lifespan(_app):
    global publisher
    db.init()
    if mqtt.configured():
        publisher = mqtt.Publisher(VERSION)
        publisher.start()
    thread = None
    if os.environ.get("DISABLE_WORKER") != "1" and (ha.configured() or publisher):
        thread = threading.Thread(target=worker, name="worker", daemon=True)
        thread.start()
    yield
    _stop.set()
    _wake.set()
    if publisher:
        publisher.stop()


app = FastAPI(title="Stromverbrauch", version=VERSION, lifespan=lifespan)


# ---------------------------------------------------------------- Laden

def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def load_readings() -> list[calc.Reading]:
    return [calc.Reading(ts=_parse_ts(r["ts"]), value=r["value"], kind=r["kind"], id=r["id"])
            for r in db.rows("SELECT * FROM readings ORDER BY ts")]


def load_tariffs() -> list[calc.Tariff]:
    return [calc.Tariff(valid_from=date.fromisoformat(r["valid_from"]), energy_ct=r["energy_ct"],
                        tax_ct=r["tax_ct"], base_eur_year=r["base_eur_year"], vat_pct=r["vat_pct"],
                        name=r["name"] or "")
            for r in db.rows("SELECT * FROM tariffs")]


def load_periods() -> list[calc.Period]:
    return [calc.Period(start=date.fromisoformat(r["start"]), end=date.fromisoformat(r["end"]),
                        start_value=r["start_value"])
            for r in db.rows("SELECT * FROM periods")]


def load_installments() -> list[calc.Installment]:
    return [calc.Installment(valid_from=date.fromisoformat(r["valid_from"]), amount=r["amount"],
                             due_day=r["due_day"])
            for r in db.rows("SELECT * FROM installments")]


# ---------------------------------------------------------------- Übersicht

def load_samples(entity_id: str, since: datetime) -> list[tuple[datetime, float]]:
    # etwas Vorlauf, damit der Startwert vor dem Zeitfenster bekannt ist
    rows = db.rows("SELECT ts, value FROM samples WHERE entity_id = ? AND ts >= ? ORDER BY ts",
                   (entity_id, (since - timedelta(days=2)).isoformat(timespec="seconds")))
    return [(datetime.fromisoformat(r["ts"]), r["value"]) for r in rows]


def build_summary() -> dict:
    now = datetime.now(TZ)
    s = calc.summarize(load_readings(), load_tariffs(), load_periods(), load_installments(), now)
    ents = db.rows("SELECT * FROM ha_entities ORDER BY id")
    since = now - timedelta(days=7)
    consumers = [{"name": e["name"], "samples": load_samples(e["entity_id"], since),
                  "power": ha.current_power.get(e["power_entity_id"]) if e["power_entity_id"] else None}
                 for e in ents if e["role"] == "verbraucher"]
    if consumers:
        s["consumers"] = calc.consumer_stats(consumers, (s.get("rate") or {}).get("recent"), now)
    temp = next((e for e in ents if e["role"] == "aussentemperatur"), None) or \
        next((e for e in ents if e["role"] == "wetter"), None)
    if temp:
        smp = load_samples(temp["entity_id"], since)
        if smp:
            s["weather"] = {"name": temp["name"], "now": smp[-1][1],
                            "avg_7d": calc.mean_value(smp, since, now)}
    return s


@app.get("/api/summary")
def summary():
    return build_summary()


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


# ---------------------------------------------------------------- Ablesungen

@app.get("/api/readings")
def list_readings():
    return db.rows("SELECT * FROM readings ORDER BY ts DESC")


@app.post("/api/readings")
async def add_reading(
    value: float = Form(...),
    ts: Optional[str] = Form(None),
    kind: str = Form("ablesung"),
    note: Optional[str] = Form(None),
    force: bool = Form(False),
    photo: Optional[UploadFile] = File(None),
):
    if kind not in ("ablesung", "versorger", "schaetzung"):
        raise HTTPException(400, "Unbekannte Art der Ablesung")
    when = _parse_ts(ts) if ts else datetime.now(TZ)
    new = calc.Reading(ts=when, value=value, kind=kind)
    warnings = calc.check_new_reading(load_readings(), new) if kind != "schaetzung" else []
    if warnings and not force:
        return JSONResponse(status_code=409, content={"warnings": warnings})

    photo_name = None
    if photo is not None and photo.filename:
        ext = Path(photo.filename).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".heic", ".webp"):
            raise HTTPException(400, "Nur Bilddateien erlaubt")
        photo_name = f"{when:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}{ext}"
        (db.PHOTO_DIR / photo_name).write_bytes(await photo.read())

    row_id = db.insert("readings", {"ts": when.isoformat(timespec="seconds"), "value": value,
                                    "kind": kind, "note": note, "photo": photo_name})
    publish_now()
    return {"id": row_id, "warnings": warnings}


@app.delete("/api/readings/{row_id}")
def delete_reading(row_id: int):
    r = db.rows("SELECT photo FROM readings WHERE id = ?", (row_id,))
    if r and r[0]["photo"]:
        (db.PHOTO_DIR / r[0]["photo"]).unlink(missing_ok=True)
    db.delete("readings", row_id)
    publish_now()
    return {"ok": True}


@app.get("/api/photos/{name}")
def get_photo(name: str):
    path = (db.PHOTO_DIR / name).resolve()
    if path.parent != db.PHOTO_DIR.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---------------------------------------------------------------- Einstellungen

class TariffIn(BaseModel):
    valid_from: date
    name: Optional[str] = None
    energy_ct: float = Field(gt=0)
    tax_ct: float = Field(ge=0, default=2.05)
    base_eur_year: float = Field(ge=0)
    vat_pct: float = Field(ge=0, default=19)


class PeriodIn(BaseModel):
    start: date
    end: date
    start_value: float
    note: Optional[str] = None


class InstallmentIn(BaseModel):
    valid_from: date
    amount: float = Field(ge=0)
    due_day: int = Field(ge=1, le=31, default=1)


@app.get("/api/config")
def get_config():
    return {
        "tariffs": db.rows("SELECT * FROM tariffs ORDER BY valid_from DESC"),
        "periods": db.rows("SELECT * FROM periods ORDER BY start DESC"),
        "installments": db.rows("SELECT * FROM installments ORDER BY valid_from DESC"),
    }


def _store(table: str, item: BaseModel) -> dict:
    data = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in item.model_dump().items()}
    return {"id": db.insert(table, data)}


@app.post("/api/tariffs")
def add_tariff(t: TariffIn):
    return _store("tariffs", t)


@app.post("/api/periods")
def add_period(p: PeriodIn):
    if p.end <= p.start:
        raise HTTPException(400, "Ende muss nach dem Beginn liegen")
    return _store("periods", p)


@app.post("/api/installments")
def add_installment(i: InstallmentIn):
    return _store("installments", i)


@app.delete("/api/{table}/{row_id}")
def delete_row(table: str, row_id: int):
    if table not in ("tariffs", "periods", "installments", "ha_entities"):
        raise HTTPException(404)
    if table == "ha_entities":
        r = db.rows("SELECT entity_id FROM ha_entities WHERE id = ?", (row_id,))
        if r:
            db.execute("DELETE FROM samples WHERE entity_id = ?", (r[0]["entity_id"],))
            db.execute("DELETE FROM kv WHERE key = ?", (f"backfilled:{r[0]['entity_id']}",))
    db.delete(table, row_id)
    publish_now()
    return {"ok": True}


# ---------------------------------------------------------------- Home Assistant

class HAEntityIn(BaseModel):
    role: str
    name: str = Field(min_length=1)
    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    power_entity_id: Optional[str] = Field(default=None, pattern=r"^[a-z_]+\.[a-z0-9_]+$")


@app.get("/api/ha/status")
def ha_status():
    st = json.loads(db.kv_get("ha_status") or "null")
    return {
        "roles": ha.ROLES,
        "ha": {"configured": ha.configured(), "url": os.environ.get("HA_URL"), **(st or {})},
        "mqtt": {"configured": mqtt.configured(),
                 "connected": bool(publisher and publisher.connected),
                 "error": publisher.last_error if publisher else None,
                 "last_publish": publisher.last_publish if publisher else None},
    }


@app.get("/api/ha/entities")
def ha_entities():
    ents = db.rows("SELECT * FROM ha_entities ORDER BY role, id")
    for e in ents:
        last = db.rows("SELECT ts, value FROM samples WHERE entity_id = ? ORDER BY ts DESC LIMIT 1",
                       (e["entity_id"],))
        e["last"] = last[0] if last else None
        e["samples"] = db.rows("SELECT COUNT(*) AS n FROM samples WHERE entity_id = ?",
                               (e["entity_id"],))[0]["n"]
        e["power"] = ha.current_power.get(e["power_entity_id"]) if e["power_entity_id"] else None
    return ents


@app.post("/api/ha/entities")
def add_ha_entity(e: HAEntityIn):
    if e.role not in ha.ROLES:
        raise HTTPException(400, "Unbekannte Rolle")
    warning = None
    if ha.configured():
        try:
            client = ha.HAClient()
            for eid, role in ((e.entity_id, e.role), (e.power_entity_id, "leistung")):
                if eid and ha.value_of(client.state(eid), role) is None:
                    raise HTTPException(400, f"{eid} gibt es in Home Assistant nicht oder hat keinen Zahlenwert")
        except HTTPException:
            raise
        except Exception as ex:  # noqa: BLE001
            warning = f"Home Assistant gerade nicht erreichbar ({ex}). Gespeichert, wird später geprüft."
    row_id = db.insert("ha_entities", e.model_dump())
    _wake.set()  # sofort abfragen und Verlauf übernehmen
    return {"id": row_id, "warning": warning}



# ---------------------------------------------------------------- Import / Export

@app.get("/api/export")
def export():
    data = db.export_all()
    data["exported_at"] = datetime.now(TZ).isoformat()
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="stromverbrauch-{date.today()}.json"'})


@app.post("/api/import")
async def import_data(file: UploadFile = File(...), replace: bool = Form(False)):
    import json
    try:
        data = json.loads(await file.read())
    except ValueError:
        raise HTTPException(400, "Keine gültige JSON-Datei")
    return {"imported": db.import_all(data, replace=replace)}


# ---------------------------------------------------------------- Web-App

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
