# Stromverbrauch

Kleine selbst gehostete Web-App, mit der du deinen Stromzähler ablesen und deinen Verbrauch hochrechnen kannst. Sie zeigt:

- deinen aktuellen Verbrauch (kWh/Tag) und die Abweichung zur Ablesung davor
- Hochrechnungen für Tag, Monat, 12 Monate und den laufenden Abrechnungszeitraum
- die voraussichtliche Nachzahlung bzw. das voraussichtliche Guthaben inkl. Spanne
- einen empfohlenen Abschlag und den Tagesverbrauch, bei dem die Abschläge genau reichen
- Tarifwechsel und Preisänderungen mitten im Zeitraum (tagesgenau)

Die Ablesungen prüft die App auf Plausibilität (Tippfehler, rückwärts laufender Zähler). Zu jeder Ablesung kannst du ein Foto als Beleg speichern. Schätzungen des Versorgers dienen nur als Anfangsstand der Abrechnung und fließen nicht in die Prognose ein.

> **Stufe 1** von 4. Danach folgen: HA-Integration (MQTT-Sensoren, Ablese-Erinnerung), ein saisonales Modell (Warmwasser/Durchlauferhitzer, Kaltwassertemperatur, Grundlast) und Extras (Foto-Erkennung, Tarifvergleich).

## Installation auf dem OMV (Docker Compose)

1. In OMV unter **Dienste → Compose → Dateien** eine neue Datei anlegen und den Inhalt von [`docker-compose.yml`](docker-compose.yml) einfügen.
2. Den Datenpfad (`/srv/dev-disk-by-uuid-XXXX/appdata/stromverbrauch`) an deine Platte anpassen und bei Bedarf den Port `8088`.
3. **Starten** („Up“). Die App läuft dann unter `http://<omv-ip>:8088`.
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

1. Im Cloudflare-Dashboard unter **Zero Trust → Networks → Tunnels** beim bestehenden Tunnel einen weiteren *Public Hostname* anlegen, z. B. `strom.deine-domain.de` → `http://<omv-ip>:8088`.
2. Für den neuen Hostnamen dieselbe mTLS-Regel bzw. Access-Policy aktivieren wie für Home Assistant (Gerätezertifikat).
3. Auf dem Handy `https://strom.deine-domain.de` öffnen und **zum Home-Bildschirm hinzufügen**. Die App startet dann im Vollbild.

Die App hat **keine eigene Anmeldung**. Sie ist durch dein Heimnetz und den Tunnel mit Zertifikat geschützt. Gib den Port nicht per Portweiterleitung frei.

### Als Seite in Home Assistant

**Einstellungen → Dashboards → Dashboard hinzufügen → Webseite**, dann die URL `https://strom.deine-domain.de` eintragen.

Ob die HA-App das Gerätezertifikat auch für diese eingebettete Seite mitschickt, hängt von der App-Version ab. Falls die Seite dort leer bleibt, nutze die Web-App vom Home-Bildschirm.

### iOS-Kurzbefehl „Strom ablesen“

Kurzbefehle → neuer Kurzbefehl → Aktion **URL öffnen**: `https://strom.deine-domain.de/#read`. Den Kurzbefehl kannst du auf den Home-Bildschirm legen oder per „Hey Siri, Strom ablesen“ starten.

## Daten & Backup

Alles liegt im gemounteten Ordner: `strom.db` (SQLite) und `photos/`. Diesen Ordner nimmst du in dein OMV-Backup auf. Zusätzlich kannst du die Daten in der App als JSON exportieren (ohne Fotos).

## Wie gerechnet wird

- **Bruttopreis:** (Arbeitspreis + Stromsteuer) × (1 + MwSt.). Den Grundpreis rechnet die App tagesgenau.
- **Aktueller Verbrauch:** Durchschnitt der letzten 14 Tage vor der jüngsten Ablesung. Zwischen zwei Ablesungen wird linear interpoliert.
- **Prognose Abrechnung:** bisheriger Verbrauch seit dem Anfangsstand plus aktueller Verbrauch × verbleibende Tage. Die Spanne ergibt sich aus dem 14- und dem 90-Tage-Trend.
- **Empfohlener Abschlag:** (Prognose − bereits gezahlte Abschläge) ÷ offene Abschläge, aufgerundet.
- **Abweichung:** Verbrauch seit der letzten Ablesung, die mindestens 18 Stunden zurückliegt, verglichen mit den 14 Tagen davor.

## Entwicklung

```bash
pip install -r requirements.txt pytest httpx
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
pytest
```
