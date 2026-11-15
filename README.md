# Stromverbrauch

Kleine selbst gehostete Web-App, mit der du deinen Stromzähler ablesen und deinen Verbrauch hochrechnen kannst. Sie zeigt:

- deinen aktuellen Verbrauch (kWh/Tag) und die Abweichung zur Ablesung davor
- Hochrechnungen für Tag, Monat, 12 Monate und den laufenden Abrechnungszeitraum
- die voraussichtliche Nachzahlung bzw. das voraussichtliche Guthaben inkl. Spanne
- einen empfohlenen Abschlag und den Tagesverbrauch, bei dem die Abschläge genau reichen
- Tarifwechsel und Preisänderungen mitten im Zeitraum (tagesgenau)

Die Ablesungen prüft die App auf Plausibilität (Tippfehler, rückwärts laufender Zähler). Zu jeder Ablesung kannst du ein Foto als Beleg speichern. Schätzungen des Versorgers dienen nur als Anfangsstand der Abrechnung und fließen nicht in die Prognose ein.

> **Stufe 2** von 4: Erfassung und Abrechnung (1) sowie die Anbindung an Home Assistant (2) sind fertig. Als Nächstes kommen ein saisonales Modell (Warmwasser/Durchlauferhitzer, Kaltwassertemperatur, Grundlast) und Extras (Foto-Erkennung, Tarifvergleich).

## Installation auf dem OMV (Docker Compose)

1. In OMV unter **Dienste → Compose → Dateien** eine neue Datei anlegen und den Inhalt von [`docker-compose.yml`](docker-compose.yml) einfügen.
2. Den Datenpfad (`/srv/dev-disk-by-uuid-XXXX/appdata/stromverbrauch`) an deine Platte anpassen und bei Bedarf den Port `8765`.
3. **Starten** („Up“). Die App läuft dann unter `http://<omv-ip>:8765`.
4. **Updates holen:** in OMV „Pull“ und dann „Up“ (oder automatisch per Watchtower).

Das Image wird bei jedem Push auf `main` automatisch von GitHub Actions gebaut, für amd64 und arm64: `ghcr.io/llarsde/stromverbrauch:latest`.

> Ist das Repository privat, muss das Paket unter GitHub → *Packages* → *stromverbrauch* → *Package settings* entweder öffentlich gestellt werden, oder du meldest den OMV einmalig mit `docker login ghcr.io` und einem Token (Berechtigung `read:packages`) an.

## Erste Einrichtung

Unter **Einstellungen** legst du einmalig an (alle Preise netto, wie auf der Rechnung):

- **Tarif:** Arbeitspreis (ct/kWh), Stromsteuer (ct/kWh), Grundpreis (€/Jahr), MwSt. Bei einer Preisänderung legst du einfach einen neuen Tarif mit „gültig ab“ an.
- **Abrechnungszeitraum:** Beginn, Ende und der Anfangsstand, den der Versorger angesetzt hat (auch wenn er geschätzt war).
- **Abschlag:** Betrag brutto, gültig ab, Fälligkeitstag.

Alternativ kannst du alles als JSON-Datei importieren (Einstellungen → Daten). Das Format entspricht dem Export.

## Zugriff von unterwegs (Cloudflare-Tunnel)

1. Im Cloudflare-Dashboard unter **Zero Trust → Networks → Tunnels** beim bestehenden Tunnel einen weiteren *Public Hostname* anlegen, z. B. `strom.deine-domain.de` → `http://<omv-ip>:8765`.
2. Für den neuen Hostnamen dieselbe mTLS-Regel bzw. Access-Policy aktivieren wie für Home Assistant (Gerätezertifikat).
3. Auf dem Handy `https://strom.deine-domain.de` öffnen und **zum Home-Bildschirm hinzufügen**. Die App startet dann im Vollbild.

Die App hat **keine eigene Anmeldung**. Sie ist durch dein Heimnetz und den Tunnel mit Zertifikat geschützt. Gib den Port nicht per Portweiterleitung frei.

### Als Seite in Home Assistant

**Einstellungen → Dashboards → Dashboard hinzufügen → Webseite**, dann die URL `https://strom.deine-domain.de` eintragen.

Ob die HA-App das Gerätezertifikat auch für diese eingebettete Seite mitschickt, hängt von der App-Version ab. Falls die Seite dort leer bleibt, nutze die Web-App vom Home-Bildschirm.

### iOS-Kurzbefehl „Strom ablesen“

Kurzbefehle → neuer Kurzbefehl → Aktion **URL öffnen**: `https://strom.deine-domain.de/#read`. Den Kurzbefehl kannst du auf den Home-Bildschirm legen oder per „Hey Siri, Strom ablesen“ starten.

## Home Assistant (Stufe 2)

Die App arbeitet mit Home Assistant in beide Richtungen:

- **HA → App:** Die App liest alle 5 Minuten die Sensoren, die du unter *Einstellungen → Home Assistant* verknüpfst. Das sind Messsteckdosen (Energie in kWh und optional Leistung in W), das Wetter oder ein Außentemperatursensor und später auch die Armatur- und Badsensoren. Beim Verknüpfen übernimmt sie den Verlauf der letzten 30 Tage aus HA. In der Übersicht siehst du dann, welchen Anteil jede Steckdose am Gesamtverbrauch hat.
- **App → HA:** Per MQTT-Discovery legt die App in HA automatisch das Gerät **Stromverbrauch** mit diesen Sensoren an:

| Entität | Inhalt |
|---|---|
| `sensor.strom_zaehlerstand` | letzte Ablesung (kWh), geeignet für das **Energie-Dashboard** |
| `sensor.strom_zaehlerstand_geschaetzt` | hochgerechneter aktueller Zählerstand |
| `sensor.strom_verbrauch_pro_tag` / `sensor.strom_kosten_pro_tag` | aktueller Verbrauch und Kosten pro Tag |
| `sensor.strom_abweichung` | Abweichung zur Vorperiode in % |
| `sensor.strom_prognose_monat_kwh` / `_eur`, `sensor.strom_prognose_jahr_kwh` | Hochrechnungen |
| `sensor.strom_prognose_abrechnung_kwh` / `_eur` | Prognose für den Abrechnungszeitraum |
| `sensor.strom_saldo` | Guthaben (+) bzw. Nachzahlung (−) |
| `sensor.strom_empfohlener_abschlag`, `sensor.strom_break_even` | Abschlagsempfehlung, Verbrauchsgrenze ohne Nachzahlung |
| `sensor.strom_grundlast` | gemessene Grundlast in W (Mittelwert) |
| `sensor.strom_letzte_ablesung`, `sensor.strom_tage_seit_ablesung` | für die Ablese-Erinnerung |

### Einrichtung

1. **In HA einen Benutzer anlegen** (z. B. `stromverbrauch`, „Nur lokal anmelden“). Das Mosquitto-Add-on akzeptiert HA-Benutzer automatisch.
2. **Token erstellen:** Profil → Sicherheit → *Langlebige Zugangstoken*.
3. **In OMV** unter Compose → Dateien → *stromverbrauch* im Feld **Umgebung** (`.env`) eintragen (Vorlage: [`.env.example`](.env.example)):
   ```
   HA_URL=https://ha.example.org
   HA_TOKEN=…
   MQTT_HOST=192.168.x.x
   MQTT_PASSWORD=…
   ```
   Die `docker-compose.yml` greift über `${…}` darauf zu. Zugangsdaten und Adressen landen so nicht im Repository.
4. Container neu starten. Unter *Einstellungen → Home Assistant* sollten beide Punkte grün sein. Danach die Sensoren verknüpfen.

| Variable | Standard | Bedeutung |
|---|---|---|
| `HA_URL` | – | Adresse von Home Assistant, z. B. `https://ha.example.org` oder `http://192.168.x.x:8123` |
| `HA_TOKEN` | – | langlebiger Zugangstoken |
| `HA_VERIFY_SSL` | `true` | Zertifikat prüfen |
| `HA_BACKFILL_DAYS` | `30` | wie viele Tage Verlauf beim Verknüpfen übernommen werden |
| `MQTT_HOST` / `MQTT_PORT` | – / `1883` | Mosquitto |
| `MQTT_USER` / `MQTT_PASSWORD` | – | HA-Benutzer für MQTT |
| `MQTT_TOPIC` | `stromverbrauch` | Basis-Topic der App |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Discovery-Präfix von HA |
| `POLL_SECONDS` | `300` | Abfrage- und Sendeintervall |

> **DNS-Hinweis:** Eine Domain in `HA_URL` funktioniert nur, wenn der OMV (und damit der Container) über AdGuard auflöst und so die **interne** Adresse bekommt. Sonst geht die Anfrage über Cloudflare und wird vom Gerätezertifikat geblockt. In dem Fall `HA_URL=http://<HA-IP>:8123` verwenden.

### Ablese-Erinnerung als Automation

Diese Automation in HA anlegen (Einstellungen → Automationen → ⋮ → *In YAML bearbeiten*) und die beiden `notify`-Dienste an die Namen deiner Handys anpassen:

```yaml
alias: Strom ablesen – Erinnerung
triggers:
  - trigger: time
    at: "19:00:00"
conditions:
  - condition: numeric_state
    entity_id: sensor.strom_tage_seit_ablesung
    above: 0.9        # tägliche Phase – später z. B. 6.9 für wöchentlich
actions:
  - action: notify.mobile_app_iphone          # anpassen
    data: &nachricht
      title: Strom ablesen
      message: >-
        Letzte Ablesung vor {{ states('sensor.strom_tage_seit_ablesung') | float(0) | round(0) | int }} Tag(en).
      data:
        url: https://strom.example.org/#read          # iOS
        clickAction: https://strom.example.org/#read  # Android
  - action: notify.mobile_app_android         # anpassen
    data: *nachricht
mode: single
```

Durch Tippen auf die Mitteilung öffnet sich direkt die Ablese-Seite. Die URL passt du an deinen Cloudflare-Hostnamen an.

## Daten & Backup

Alles liegt im gemounteten Ordner: `strom.db` (SQLite) und `photos/`. Diesen Ordner nimmst du in dein OMV-Backup auf. Zusätzlich kannst du die Daten in der App als JSON exportieren (ohne Fotos).

## Grundlast

Zeigt dein Zähler die aktuelle Leistung an (bei modernen Messeinrichtungen nach Freischaltung per PIN), kannst du sie beim Ablesen mit eintragen. Hakst du zusätzlich **„Nur Grundlast aktiv“** an (Durchlauferhitzer, Herd und Waschmaschine sind also aus), wertet die App diese Messungen als Grundlast aus: Mittelwert in W, kWh pro Tag, Anteil am Verbrauch und Kosten pro Jahr. Miss mehrmals zu verschiedenen Zeiten, weil Kühlschrank und Gefrierfach takten.

## Wie gerechnet wird

- **Bruttopreis:** (Arbeitspreis + Stromsteuer) × (1 + MwSt.). Den Grundpreis rechnet die App tagesgenau.
- **Aktueller Verbrauch:** Durchschnitt der letzten 14 Tage vor der jüngsten Ablesung. Zwischen zwei Ablesungen wird linear interpoliert.
- **Prognose Abrechnung:** bisheriger Verbrauch seit dem Anfangsstand plus aktueller Verbrauch × verbleibende Tage. Die Spanne ergibt sich aus dem 14- und dem 90-Tage-Trend.
- **Empfohlener Abschlag:** (Prognose − bereits gezahlte Abschläge) ÷ offene Abschläge, aufgerundet.
- **Abweichung:** Verbrauch seit der letzten Ablesung, die mindestens 18 Stunden zurückliegt, verglichen mit den 14 Tagen davor.

## Entwicklung

```bash
pip install -r requirements.txt pytest
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
pytest
```
