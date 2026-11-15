"""Werte der App per MQTT-Discovery als Sensoren in Home Assistant bereitstellen."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

import paho.mqtt.client as paho

log = logging.getLogger("stromverbrauch.mqtt")

TOPIC = os.environ.get("MQTT_TOPIC", "stromverbrauch")
PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
STATE_TOPIC = f"{TOPIC}/state"
AVAIL_TOPIC = f"{TOPIC}/status"

# key, Name, Einheit, device_class, state_class, Icon, Nachkommastellen
SENSORS = [
    ("zaehlerstand", "Zählerstand (letzte Ablesung)", "kWh", "energy", "total_increasing", None, 0),
    ("zaehlerstand_geschaetzt", "Zählerstand (geschätzt)", "kWh", "energy", "total", None, 1),
    ("verbrauch_pro_tag", "Verbrauch pro Tag", "kWh", None, "measurement", "mdi:lightning-bolt", 2),
    ("kosten_pro_tag", "Kosten pro Tag", "EUR", "monetary", None, None, 2),
    ("abweichung", "Abweichung zur Vorperiode", "%", None, "measurement", "mdi:trending-up", 0),
    ("prognose_monat_kwh", "Prognose Monat", "kWh", "energy", None, None, 0),
    ("prognose_monat_eur", "Prognose Monat Kosten", "EUR", "monetary", None, None, 0),
    ("prognose_jahr_kwh", "Prognose 12 Monate", "kWh", "energy", None, None, 0),
    ("prognose_abrechnung_kwh", "Prognose Abrechnungszeitraum", "kWh", "energy", None, None, 0),
    ("prognose_abrechnung_eur", "Prognose Abrechnung Kosten", "EUR", "monetary", None, None, 0),
    ("saldo", "Prognose Guthaben (+) / Nachzahlung (−)", "EUR", "monetary", None, "mdi:scale-balance", 0),
    ("empfohlener_abschlag", "Empfohlener Abschlag", "EUR", "monetary", None, "mdi:cash-sync", 0),
    ("break_even", "Verbrauchsgrenze ohne Nachzahlung", "kWh", None, None, "mdi:speedometer", 2),
    ("grundlast", "Grundlast (gemessen)", "W", "power", "measurement", None, 0),
    ("letzte_ablesung", "Letzte Ablesung", None, "timestamp", None, None, None),
    ("tage_seit_ablesung", "Tage seit letzter Ablesung", "d", "duration", "measurement", None, 1),
]


def configured() -> bool:
    return bool(os.environ.get("MQTT_HOST"))


def state_from_summary(s: dict, now: datetime) -> dict:
    """Übersetzt die Zusammenfassung in die Sensorwerte."""
    out: dict = {}
    lr = s.get("last_reading")
    if lr:
        out["zaehlerstand"] = lr["value"]
        out["letzte_ablesung"] = lr["ts"]
        out["tage_seit_ablesung"] = round((now - datetime.fromisoformat(lr["ts"])).total_seconds() / 86400, 2)
    if s.get("estimated_now") is not None:
        out["zaehlerstand_geschaetzt"] = round(s["estimated_now"], 1)
    if s.get("rate"):
        out["verbrauch_pro_tag"] = round(s["rate"]["recent"], 2)
    if s.get("day"):
        out["kosten_pro_tag"] = round(s["day"]["eur"], 2)
    if s.get("deviation"):
        out["abweichung"] = round(s["deviation"]["percent"], 1)
    m = s.get("month") or {}
    if m.get("kwh_projected") is not None:
        out["prognose_monat_kwh"] = round(m["kwh_projected"])
    if m.get("eur_projected") is not None:
        out["prognose_monat_eur"] = round(m["eur_projected"], 2)
    if s.get("year"):
        out["prognose_jahr_kwh"] = round(s["year"]["kwh"])
    if s.get("baseload"):
        out["grundlast"] = round(s["baseload"]["mean_w"])
    p = s.get("period")
    if p:
        out["prognose_abrechnung_kwh"] = round(p["kwh_projected"])
        out["prognose_abrechnung_eur"] = round(p["eur_projected"], 2)
        out["saldo"] = round(p["balance"], 2)
        if p.get("recommended_installment") is not None:
            out["empfohlener_abschlag"] = p["recommended_installment"]
        if p.get("break_even_rate") is not None:
            out["break_even"] = round(p["break_even_rate"], 2)
    return out


class Publisher:
    def __init__(self, version: str = "dev"):
        self.version = version
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_publish: Optional[str] = None
        c = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f"{TOPIC}-app")
        if os.environ.get("MQTT_USER"):
            c.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASSWORD"))
        c.will_set(AVAIL_TOPIC, "offline", retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        self.client = c

    def start(self) -> None:
        host, port = os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883"))
        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        if self.connected:
            self.client.publish(AVAIL_TOPIC, "offline", retain=True).wait_for_publish(2)
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            self.connected = False
            self.last_error = f"Verbindung abgelehnt: {reason_code}"
            log.warning(self.last_error)
            return
        self.connected = True
        self.last_error = None
        log.info("MQTT verbunden")
        self.publish_discovery()
        client.publish(AVAIL_TOPIC, "online", retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        if reason_code and reason_code.is_failure:
            self.last_error = f"Verbindung getrennt: {reason_code}"

    def publish_discovery(self) -> None:
        device = {"identifiers": [TOPIC], "name": "Stromverbrauch", "manufacturer": "llarsde",
                  "model": "Stromverbrauch-App", "sw_version": self.version}
        for key, name, unit, dclass, sclass, icon, precision in SENSORS:
            cfg = {
                "name": name, "unique_id": f"{TOPIC}_{key}", "default_entity_id": f"sensor.strom_{key}",
                "state_topic": STATE_TOPIC, "value_template": f"{{{{ value_json.{key} }}}}",
                "availability_topic": AVAIL_TOPIC, "device": device,
            }
            if unit:
                cfg["unit_of_measurement"] = unit
            if dclass:
                cfg["device_class"] = dclass
            if sclass:
                cfg["state_class"] = sclass
            if icon:
                cfg["icon"] = icon
            if precision is not None:
                cfg["suggested_display_precision"] = precision
            self.client.publish(f"{PREFIX}/sensor/{TOPIC}/{key}/config", json.dumps(cfg), retain=True)

    def publish_state(self, state: dict) -> bool:
        if not self.connected:
            return False
        # fehlende Werte als None senden, damit HA "unbekannt" anzeigt statt alter Werte
        payload = {key: state.get(key) for key, *_ in SENSORS}
        self.client.publish(STATE_TOPIC, json.dumps(payload), retain=True)
        self.last_publish = datetime.now().astimezone().isoformat(timespec="seconds")
        return True
