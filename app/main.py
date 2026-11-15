"""Stromverbrauch – Web-API und Auslieferung der Web-App."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import calc, db

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
STATIC = Path(__file__).parent / "static"
VERSION = os.environ.get("APP_VERSION", "dev")

@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


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

@app.get("/api/summary")
def summary():
    return calc.summarize(load_readings(), load_tariffs(), load_periods(), load_installments(),
                          datetime.now(TZ))


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
    return {"id": row_id, "warnings": warnings}


@app.delete("/api/readings/{row_id}")
def delete_reading(row_id: int):
    r = db.rows("SELECT photo FROM readings WHERE id = ?", (row_id,))
    if r and r[0]["photo"]:
        (db.PHOTO_DIR / r[0]["photo"]).unlink(missing_ok=True)
    db.delete("readings", row_id)
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
    if table not in ("tariffs", "periods", "installments"):
        raise HTTPException(404)
    db.delete(table, row_id)
    return {"ok": True}


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
