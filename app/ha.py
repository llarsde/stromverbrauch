"""Anbindung an Home Assistant: Zustände und Verlauf über die REST-API lesen."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from . import db

log = logging.getLogger("stromverbrauch.ha")
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
BACKFILL_DAYS = int(os.environ.get("HA_BACKFILL_DAYS", "30"))

ROLES = {
    "verbraucher": "Verbraucher (Messsteckdose)",
    "wetter": "Wetter",
    "aussentemperatur": "Außentemperatur",
    "kaltwasser": "Kaltwasser-Temperatur (Armatur)",
    "warmwasser": "Warmwasser-Temperatur (Armatur)",
    "bad_feuchte": "Luftfeuchte Bad",
}


def configured() -> bool:
    return bool(os.environ.get("HA_URL") and os.environ.get("HA_TOKEN"))


class HAClient:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None):
        url = (url or os.environ.get("HA_URL", "")).rstrip("/")
        token = token or os.environ.get("HA_TOKEN", "")
        verify = os.environ.get("HA_VERIFY_SSL", "true").lower() not in ("0", "false", "no")
        self.client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"},
                                   timeout=20, verify=verify, transport=transport)

    def ping(self) -> str:
        r = self.client.get("/api/")
        r.raise_for_status()
        return r.json().get("message", "ok")

    def state(self, entity_id: str) -> Optional[dict]:
        r = self.client.get(f"/api/states/{entity_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def history(self, entity_id: str, start: datetime, with_attributes: bool = False) -> list[dict]:
        params = {"filter_entity_id": entity_id, "end_time": datetime.now(TZ).isoformat()}
        if not with_attributes:
            params["no_attributes"] = ""
        r = self.client.get(f"/api/history/period/{start.isoformat()}", params=params)
        r.raise_for_status()
        data = r.json()
        return data[0] if data else []


def value_of(state: Optional[dict], role: str) -> Optional[float]:
    """Zahlenwert aus einem HA-Zustand. Beim Wetter ist es die Temperatur aus den Attributen."""
    if not state:
        return None
    raw = state.get("attributes", {}).get("temperature") if role == "wetter" else state.get("state")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None  # "unavailable", "unknown" …
    return v


def _ts(s: str) -> str:
    return datetime.fromisoformat(s).astimezone(TZ).isoformat(timespec="seconds")


def backfill(client: HAClient, ent: dict) -> int:
    """Verlauf der letzten Tage aus HA übernehmen (einmal pro Entität)."""
    key = f"backfilled:{ent['entity_id']}"
    if db.kv_get(key):
        return 0
    start = datetime.now(TZ) - timedelta(days=BACKFILL_DAYS)
    states = client.history(ent["entity_id"], start, with_attributes=ent["role"] == "wetter")
    points = []
    for st in states:
        v = value_of(st, ent["role"])
        if v is not None:
            points.append((ent["entity_id"], _ts(st.get("last_updated") or st["last_changed"]), v))
    n = db.add_samples(points)
    db.kv_set(key, datetime.now(TZ).isoformat(timespec="seconds"))
    log.info("Verlauf für %s übernommen: %d Werte", ent["entity_id"], n)
    return n


# Aktuelle Leistung (W) je Entität – nur im Speicher, wird nicht historisiert
current_power: dict[str, float] = {}


def poll(client: HAClient) -> dict:
    """Alle konfigurierten Entitäten einmal abfragen und speichern."""
    entities = db.rows("SELECT * FROM ha_entities")
    now = datetime.now(TZ).isoformat(timespec="seconds")
    points, missing = [], []
    for ent in entities:
        try:
            backfill(client, ent)
        except httpx.HTTPError as e:
            log.warning("Verlauf für %s nicht abrufbar: %s", ent["entity_id"], e)
        v = value_of(client.state(ent["entity_id"]), ent["role"])
        if v is None:
            missing.append(ent["entity_id"])
        else:
            points.append((ent["entity_id"], now, v))
        if ent.get("power_entity_id"):
            p = value_of(client.state(ent["power_entity_id"]), "leistung")
            if p is not None:
                current_power[ent["power_entity_id"]] = p
    db.add_samples(points)
    return {"stored": len(points), "missing": missing}
