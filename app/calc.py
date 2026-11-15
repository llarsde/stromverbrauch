"""Reine Berechnungslogik (ohne Datenbank), damit sie gut testbar ist.

Stufe 1: lineares Modell auf Basis des aktuellen Tagesverbrauchs.
Stufe 3 ersetzt `expected_rate` durch ein saisonales Modell.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

DAY = 86400.0
RECENT_WINDOW_DAYS = 14
LONG_WINDOW_DAYS = 90
MAX_PLAUSIBLE_RATE = 80.0  # kWh/Tag – darüber ist es fast sicher ein Tippfehler


@dataclass
class Reading:
    ts: datetime
    value: float
    kind: str = "ablesung"
    id: Optional[int] = None


@dataclass
class Tariff:
    valid_from: date
    energy_ct: float
    tax_ct: float
    base_eur_year: float
    vat_pct: float = 19.0
    name: str = ""

    @property
    def price_kwh(self) -> float:
        """Arbeitspreis brutto in €/kWh (inkl. Stromsteuer und MwSt.)."""
        return (self.energy_ct + self.tax_ct) / 100 * (1 + self.vat_pct / 100)

    @property
    def base_year(self) -> float:
        """Grundpreis brutto in €/Jahr."""
        return self.base_eur_year * (1 + self.vat_pct / 100)


@dataclass
class Period:
    start: date
    end: date  # inklusive
    start_value: float


@dataclass
class Installment:
    valid_from: date
    amount: float
    due_day: int = 1


# ---------------------------------------------------------------- Hilfen

def days_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / DAY


def day_start(d: date, tz) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz)


def real_readings(readings: list[Reading]) -> list[Reading]:
    """Nur echte Ablesungen (keine Schätzungen des Versorgers), zeitlich sortiert."""
    return sorted((r for r in readings if r.kind != "schaetzung"), key=lambda r: r.ts)


def value_at(readings: list[Reading], t: datetime) -> Optional[float]:
    """Zählerstand zum Zeitpunkt t, linear zwischen zwei Ablesungen interpoliert.
    Außerhalb des Ablesezeitraums: None."""
    rs = real_readings(readings)
    if not rs or t < rs[0].ts or t > rs[-1].ts:
        return None
    for a, b in zip(rs, rs[1:]):
        if a.ts <= t <= b.ts:
            span = days_between(a.ts, b.ts)
            if span <= 0:
                return b.value
            return a.value + (b.value - a.value) * days_between(a.ts, t) / span
    return rs[-1].value


def rate_over_window(readings: list[Reading], window_days: float) -> Optional[dict]:
    """Durchschnittlicher Verbrauch (kWh/Tag) in den letzten `window_days` vor der
    jüngsten Ablesung. Ist das Fenster kürzer als 1 Tag, wird bis zur vorherigen
    Ablesung erweitert."""
    rs = real_readings(readings)
    if len(rs) < 2:
        return None
    last = rs[-1]
    start_t = max(last.ts - timedelta(days=window_days), rs[0].ts)
    if days_between(start_t, last.ts) < 1:
        start_t = rs[-2].ts
    span = days_between(start_t, last.ts)
    if span <= 0:
        return None
    start_v = value_at(rs, start_t)
    return {"rate": (last.value - start_v) / span, "days": span, "from": start_t, "to": last.ts}


def intervals(readings: list[Reading]) -> list[dict]:
    """Verbrauch je Ableseintervall (für das Diagramm)."""
    rs = real_readings(readings)
    out = []
    for a, b in zip(rs, rs[1:]):
        span = days_between(a.ts, b.ts)
        if span <= 0:
            continue
        out.append({
            "from": a.ts.isoformat(), "to": b.ts.isoformat(),
            "days": span, "kwh": b.value - a.value, "rate": (b.value - a.value) / span,
        })
    return out


def check_new_reading(readings: list[Reading], new: Reading) -> list[str]:
    """Plausibilitätsprüfung. Liefert Warnungen (leer = alles plausibel)."""
    warnings = []
    rs = real_readings(readings)
    before = [r for r in rs if r.ts <= new.ts]
    after = [r for r in rs if r.ts > new.ts]
    if before:
        prev = before[-1]
        if new.value < prev.value:
            warnings.append(
                f"Der Stand {new.value:g} ist kleiner als die vorherige Ablesung "
                f"({prev.value:g} am {prev.ts:%d.%m.%Y})."
            )
        else:
            span = days_between(prev.ts, new.ts)
            if span > 0:
                rate = (new.value - prev.value) / span
                if rate > MAX_PLAUSIBLE_RATE:
                    warnings.append(
                        f"Das wären {rate:.1f} kWh/Tag seit der letzten Ablesung – ungewöhnlich hoch. Tippfehler?"
                    )
    if after and new.value > after[0].value:
        warnings.append(
            f"Der Stand ist größer als die spätere Ablesung ({after[0].value:g} am {after[0].ts:%d.%m.%Y})."
        )
    return warnings


# ---------------------------------------------------------------- Tarif & Abschläge

def tariff_at(tariffs: list[Tariff], d: date) -> Optional[Tariff]:
    valid = [t for t in tariffs if t.valid_from <= d]
    if valid:
        return max(valid, key=lambda t: t.valid_from)
    return min(tariffs, key=lambda t: t.valid_from) if tariffs else None


def cost(tariffs: list[Tariff], start: date, end: date, kwh: float) -> float:
    """Bruttokosten für `kwh` gleichmäßig verteilt über [start, end] (inklusive),
    Grundpreis tagesgenau. Berücksichtigt Preisänderungen im Zeitraum."""
    total_days = (end - start).days + 1
    if total_days <= 0 or not tariffs:
        return 0.0
    eur = 0.0
    d = start
    while d <= end:
        t = tariff_at(tariffs, d)
        # Segment bis zum nächsten Tarifwechsel oder Periodenende
        nxt = [x.valid_from for x in tariffs if d < x.valid_from <= end]
        seg_end = min(nxt) - timedelta(days=1) if nxt else end
        seg_days = (seg_end - d).days + 1
        eur += kwh * seg_days / total_days * t.price_kwh
        eur += t.base_year * seg_days / 365
        d = seg_end + timedelta(days=1)
    return eur


def due_dates(installments: list[Installment], start: date, end: date) -> list[tuple[date, float]]:
    """Fälligkeiten der Abschläge im Abrechnungszeitraum (start < Fälligkeit <= end).
    Pro Monat gilt der jüngste Abschlagsplan, der zu seinem Fälligkeitstag schon gültig ist."""
    out = []
    y, m = start.year, start.month
    while installments and date(y, m, 1) <= end:
        last_day = calendar.monthrange(y, m)[1]
        best = None
        for i in installments:
            dd = date(y, m, min(i.due_day, last_day))
            if i.valid_from <= dd and (best is None or i.valid_from > best[1].valid_from):
                best = (dd, i)
        if best and start < best[0] <= end:
            out.append((best[0], best[1].amount))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


# ---------------------------------------------------------------- Zusammenfassung

def summarize(
    readings: list[Reading],
    tariffs: list[Tariff],
    periods: list[Period],
    installments: list[Installment],
    now: datetime,
) -> dict:
    tz = now.tzinfo
    rs = real_readings(readings)
    out: dict = {"now": now.isoformat(), "readings_count": len(rs)}
    if not rs:
        return out
    last = rs[-1]
    out["last_reading"] = {"ts": last.ts.isoformat(), "value": last.value,
                           "age_days": days_between(last.ts, now)}

    recent = rate_over_window(rs, RECENT_WINDOW_DAYS)
    long = rate_over_window(rs, LONG_WINDOW_DAYS)
    if not recent:
        return out
    rate = recent["rate"]
    rate_low = min(rate, long["rate"]) if long else rate
    rate_high = max(rate, long["rate"]) if long else rate
    out["rate"] = {"recent": rate, "recent_days": recent["days"],
                   "long": long["rate"] if long else None, "long_days": long["days"] if long else None}

    # Geschätzter aktueller Stand
    est_now = last.value + rate * max(0.0, days_between(last.ts, now))
    out["estimated_now"] = est_now

    # Abweichung: letztes Intervall vs. Verbrauch davor
    iv = intervals(rs)
    out["intervals"] = iv[-60:]
    # Kurze Intervalle (mehrere Ablesungen am selben Tag) sind zu verrauscht:
    # verglichen wird ab der letzten Ablesung, die mindestens 18 h zurückliegt.
    earlier = [r for r in rs[:-1] if days_between(r.ts, last.ts) >= 0.75]
    if earlier:
        anchor = earlier[-1]
        prev_rs = [r for r in rs if r.ts <= anchor.ts]
        ref = rate_over_window(prev_rs, RECENT_WINDOW_DAYS)
        last_rate = (last.value - anchor.value) / days_between(anchor.ts, last.ts)
        if ref and ref["rate"] > 0:
            out["deviation"] = {"last_rate": last_rate, "reference_rate": ref["rate"],
                                "since": anchor.ts.isoformat(),
                                "percent": (last_rate / ref["rate"] - 1) * 100}

    today = now.date()
    t_now = tariff_at(tariffs, today)
    if t_now:
        out["tariff"] = {"price_kwh": t_now.price_kwh, "base_year": t_now.base_year,
                         "name": t_now.name}
        out["day"] = {"kwh": rate, "eur": rate * t_now.price_kwh + t_now.base_year / 365}

    # Monat (Kalendermonat)
    m_start = day_start(today.replace(day=1), tz)
    m_end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    m_end = day_start(m_end_date + timedelta(days=1), tz)
    v_m_start = value_at(rs, m_start)
    month = {"label": today.strftime("%m/%Y"), "kwh_so_far": None, "kwh_projected": None}
    if v_m_start is not None:
        month["kwh_so_far"] = est_now - v_m_start
        month["kwh_projected"] = last.value - v_m_start + rate * days_between(last.ts, m_end)
    else:
        # Monatsanfang liegt vor der ersten Ablesung: Rate × Monatstage
        month["kwh_projected"] = rate * calendar.monthrange(today.year, today.month)[1]
        month["estimated_only"] = True
    if t_now:
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        month["eur_projected"] = month["kwh_projected"] * t_now.price_kwh + t_now.base_year * days_in_month / 365
    out["month"] = month

    # 12 Monate
    out["year"] = {"kwh": rate * 365}
    if t_now:
        out["year"]["eur"] = rate * 365 * t_now.price_kwh + t_now.base_year

    # Abrechnungszeitraum
    period = next((p for p in sorted(periods, key=lambda p: p.start, reverse=True)
                   if p.start <= today), None)
    if period and tariffs:
        p_end = day_start(period.end + timedelta(days=1), tz)
        remaining = max(0.0, days_between(last.ts, p_end))
        consumed = last.value - period.start_value

        def project(r):
            kwh = consumed + r * remaining
            return kwh, cost(tariffs, period.start, period.end, kwh)

        kwh, eur = project(rate)
        kwh_lo, eur_lo = project(rate_low)
        kwh_hi, eur_hi = project(rate_high)

        dues = due_dates(installments, period.start, period.end)
        total_paid_planned = sum(a for _, a in dues)
        paid_so_far = sum(a for d, a in dues if d <= today)
        open_dues = [(d, a) for d, a in dues if d > today]

        # Break-even: Tagesverbrauch, bei dem die Abschläge genau reichen
        break_even = None
        if remaining > 0 and total_paid_planned > 0:
            lo, hi = 0.0, 200.0
            for _ in range(60):
                mid = (lo + hi) / 2
                if project(mid)[1] > total_paid_planned:
                    hi = mid
                else:
                    lo = mid
            break_even = lo

        recommended = None
        if open_dues:
            recommended = math.ceil((eur - paid_so_far) / len(open_dues))

        out["period"] = {
            "start": period.start.isoformat(), "end": period.end.isoformat(),
            "start_value": period.start_value,
            "days_total": (period.end - period.start).days + 1,
            "days_remaining": remaining,
            "kwh_so_far": consumed,
            "kwh_projected": kwh, "kwh_low": kwh_lo, "kwh_high": kwh_hi,
            "eur_projected": eur, "eur_low": eur_lo, "eur_high": eur_hi,
            "installments_total": total_paid_planned,
            "installments_paid": paid_so_far,
            "installments_open": len(open_dues),
            "installment_current": open_dues[0][1] if open_dues else None,
            "balance": total_paid_planned - eur,  # + = Guthaben, − = Nachzahlung
            "balance_low": total_paid_planned - eur_hi,
            "balance_high": total_paid_planned - eur_lo,
            "break_even_rate": break_even,
            "recommended_installment": recommended,
        }
    return out
