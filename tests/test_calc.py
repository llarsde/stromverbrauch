from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app import calc

TZ = ZoneInfo("Europe/Berlin")


def dt(*a):
    return datetime(*a, tzinfo=TZ)


TARIFF = calc.Tariff(valid_from=date(2025, 1, 1), energy_ct=30.61387, tax_ct=2.05,
                     base_eur_year=143.01)
PERIOD = calc.Period(start=date(2026, 10, 9), end=date(2027, 10, 8), start_value=7229)
INST = [calc.Installment(valid_from=date(2026, 11, 1), amount=125, due_day=8)]
READINGS = [
    calc.Reading(dt(2025, 10, 9, 0, 0), 4637, "versorger"),
    calc.Reading(dt(2026, 10, 8, 23, 59), 7229, "schaetzung"),
    calc.Reading(dt(2026, 10, 29, 12, 0), 7755),
    calc.Reading(dt(2026, 11, 15, 12, 0), 7918),
]


def test_brutto_preise():
    assert TARIFF.price_kwh == pytest.approx(0.38870, abs=1e-5)
    assert TARIFF.base_year == pytest.approx(170.18, abs=0.01)


def test_rechnung_2025_26_nachgerechnet():
    # Abgerechnet: 2.592 kWh, 365 Tage → 1.177,70 € brutto
    eur = calc.cost([TARIFF], date(2025, 10, 9), date(2026, 10, 8), 2592)
    assert eur == pytest.approx(1177.70, abs=0.05)


def test_schaetzung_wird_ignoriert():
    assert all(r.kind != "schaetzung" for r in calc.real_readings(READINGS))


def test_aktuelle_rate():
    r = calc.rate_over_window(READINGS, 14)
    assert r["rate"] == pytest.approx(163 / 17, rel=1e-6)


def test_interpolation():
    v = calc.value_at(READINGS, dt(2026, 11, 6, 12, 0))
    assert v == pytest.approx(7755 + 163 * 8 / 17)


def test_abschlaege_im_zeitraum():
    dues = calc.due_dates(INST, PERIOD.start, PERIOD.end)
    assert len(dues) == 12
    assert dues[0][0] == date(2026, 11, 8) and dues[-1][0] == date(2027, 10, 8)
    assert sum(a for _, a in dues) == 1500


def test_abschlagswechsel():
    inst = [calc.Installment(date(2026, 10, 1), 101, 8), calc.Installment(date(2027, 2, 1), 130, 8)]
    dues = calc.due_dates(inst, PERIOD.start, PERIOD.end)
    assert [a for _, a in dues] == [101] * 3 + [130] * 9


def test_prognose_abrechnungszeitraum():
    s = calc.summarize(READINGS, [TARIFF], [PERIOD], INST, dt(2026, 11, 15, 12, 0))
    p = s["period"]
    assert p["kwh_so_far"] == 689
    assert p["installments_total"] == 1500
    assert p["installments_paid"] == 125
    # Break-even ab heute niedriger als am 29.10., weil schon mehr verbraucht wurde.
    # 9,59 kWh/Tag × ~327,5 Tage + 689 ≈ 3.829 kWh → ≈ 1.659 € → ≈ −159 €
    assert p["kwh_projected"] == pytest.approx(3829, abs=5)
    assert p["balance"] == pytest.approx(-159, abs=3)
    assert p["break_even_rate"] == pytest.approx(8.34, abs=0.05)
    assert p["recommended_installment"] == 140


def test_preisaenderung_im_zeitraum():
    t2 = calc.Tariff(valid_from=date(2027, 4, 9), energy_ct=40, tax_ct=2.05, base_eur_year=143.01)
    a = calc.cost([TARIFF], PERIOD.start, PERIOD.end, 3000)
    b = calc.cost([TARIFF, t2], PERIOD.start, PERIOD.end, 3000)
    assert b > a


def test_plausibilitaet():
    w = calc.check_new_reading(READINGS, calc.Reading(dt(2026, 11, 16, 12, 0), 7900))
    assert w and "kleiner" in w[0]
    w = calc.check_new_reading(READINGS, calc.Reading(dt(2026, 11, 16, 12, 0), 8918))
    assert w and "ungewöhnlich" in w[0]
    assert calc.check_new_reading(READINGS, calc.Reading(dt(2026, 11, 16, 12, 0), 7928)) == []


def test_ohne_daten():
    s = calc.summarize([], [], [], [], dt(2026, 11, 15))
    assert s["readings_count"] == 0


def test_abweichung_ignoriert_kurze_intervalle():
    rs = READINGS + [calc.Reading(dt(2026, 11, 15, 18, 0), 7930)]
    s = calc.summarize(rs, [TARIFF], [PERIOD], INST, dt(2026, 11, 15, 19, 0))
    # Vergleich ab 29.10. statt nur über die 6 Stunden seit Mittag
    assert s["deviation"]["last_rate"] == pytest.approx((7930 - 7755) / 17.25, rel=1e-3)


def test_grundlast():
    b = calc.baseload_stats([147, 120, 180], 9.6, 0.3887)
    assert b["mean_w"] == pytest.approx(149)
    assert b["kwh_day"] == pytest.approx(3.576)
    assert b["share"] == pytest.approx(37.25)
    assert b["eur_year"] == pytest.approx(3.576 * 365 * 0.3887)
