"""Tests for skills/research-facts/scripts/facts_check.py (synthetic data only)."""
import copy
import datetime as dt
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "research-facts"


def _load():
    spec = importlib.util.spec_from_file_location("facts_check", SKILL / "scripts" / "facts_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fc = _load()
TODAY = dt.date(2026, 6, 1)

FACTS = {
    "_note": "fictional",
    "revenue_2025": {"value": 12.52, "unit": "billion $", "type": "primary", "source": "Acme annual report",
                     "url": "https://example.com/ar-2025", "accessed": "2026-03-01"},
    "revenue_2024": {"value": 10.0, "unit": "billion $", "type": "primary", "source": "Acme annual report",
                     "url": "https://example.com/ar-2024", "accessed": "2026-03-01"},
    "growth": {"value": 25.2, "unit": "%", "type": "own-calculation", "source": "own",
               "calc": "(revenue_2025 - revenue_2024) / revenue_2024 * 100", "accessed": "2026-03-01"},
    "employees": {"value": 3040, "unit": "employees", "type": "news", "source": "Example News",
                  "url": "https://example.com/news", "accessed": "2026-03-02"},
    "share": {"value": 41.3, "unit": "percent", "type": "estimate", "source": "Example Research",
              "url": "https://example.com/research", "accessed": "2026-03-02"},
    "do_not_use": [{"claim": "the largest widget maker", "why": "only a press release says so"}],
}


def _codes(findings, level="FAIL"):
    return [f["code"] for f in findings if f["level"] == level]


def _check(script, facts=None, ignore=None):
    facts_obj, dnu, _ = fc.validate_facts(copy.deepcopy(facts or FACTS), TODAY)
    keys = {k for k in (facts or FACTS) if not k.startswith("_")}
    return fc.check_script(script, facts_obj, dnu, ignore or [], keys)


def _status(script, facts=None, ignore=None):
    numbers, _ = _check(script, facts, ignore)
    return [(n["text"], n["status"], n["key"]) for n in numbers]


# ---------------------------------------------------------------------------------------------------------------
# facts.json validation
# ---------------------------------------------------------------------------------------------------------------

def test_clean_pack_has_no_fail_and_info_for_news_and_estimate():
    facts, dnu, findings = fc.validate_facts(copy.deepcopy(FACTS), TODAY)
    assert _codes(findings) == []
    assert set(facts) == {"revenue_2025", "revenue_2024", "growth", "employees", "share"}
    assert sorted(_codes(findings, "INFO")) == ["estimate", "news"]
    assert len(dnu) == 1


@pytest.mark.parametrize("field,value,code", [
    ("value", "12.5", "value"),
    ("value", True, "value"),
    ("type", "blog", "type"),
    ("url", "example.com/x", "url"),
    ("url", "", "url"),
    ("accessed", "03/01/2026", "accessed"),
    ("accessed", "2026-02-30", "accessed"),
    ("source", "", "source"),
    ("unit", None, "unit"),
])
def test_schema_errors(field, value, code):
    data = copy.deepcopy(FACTS)
    data["revenue_2024"][field] = value
    _, _, findings = fc.validate_facts(data, TODAY)
    assert code in _codes(findings)


def test_accessed_in_future_fails():
    data = copy.deepcopy(FACTS)
    data["revenue_2024"]["accessed"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    _, _, findings = fc.validate_facts(data, dt.date.today())
    assert "accessed" in _codes(findings)


def test_own_calculation_needs_no_url():
    _, _, findings = fc.validate_facts(copy.deepcopy(FACTS), TODAY)
    assert not [f for f in findings if f.get("key") == "growth" and f["level"] == "FAIL"]


def test_calc_recompute_fail():
    data = copy.deepcopy(FACTS)
    data["growth"]["value"] = 30
    _, _, findings = fc.validate_facts(data, TODAY)
    msgs = [f["message"] for f in findings if f["code"] == "calc" and f["level"] == "FAIL"]
    assert msgs and "25.2" in msgs[0]


def test_calc_within_one_percent_passes():
    data = copy.deepcopy(FACTS)
    data["growth"]["value"] = 25.4  # true result 25.2, 0.8 % away
    _, _, findings = fc.validate_facts(data, TODAY)
    assert "calc" not in _codes(findings)


def test_calc_unknown_key_missing_calc_and_unsafe():
    data = copy.deepcopy(FACTS)
    data["growth"]["calc"] = "revenue_2025 / revenue_2023"
    assert "calc" in _codes(fc.validate_facts(data, TODAY)[2])
    del data["growth"]["calc"]
    assert "calc" in _codes(fc.validate_facts(data, TODAY)[2])
    data["growth"]["calc"] = "__import__('os').getcwd() + revenue_2025"
    findings = fc.validate_facts(data, TODAY)[2]
    assert "calc" in _codes(findings, "INFO")  # never evaluated: not plain arithmetic
    data["growth"]["calc"] = "round(revenue_2025 / revenue_2024 * 100 - 100, 1)"
    findings = fc.validate_facts(data, TODAY)[2]
    assert "calc" not in _codes(findings) and "calc" in _codes(findings, "INFO")  # not plain arithmetic
    data["growth"]["calc"] = "12.52 / 10 * 100 - 100"
    assert "calc" in _codes(fc.validate_facts(data, TODAY)[2])  # uses no other keys


def test_eval_calc_rejects_non_arithmetic():
    assert fc.eval_calc("(a + b) * 2 / 4", {"a": 1, "b": 3}) == 2.0
    with pytest.raises(ValueError):
        fc.eval_calc("a ** 2", {"a": 3})
    with pytest.raises(ZeroDivisionError):
        fc.eval_calc("a / 0", {"a": 3})


def test_do_not_use_schema():
    data = copy.deepcopy(FACTS)
    data["do_not_use"] = [{"why": "no claim"}]
    assert "schema" in _codes(fc.validate_facts(data, TODAY)[2])
    data["do_not_use"] = "nope"
    assert "schema" in _codes(fc.validate_facts(data, TODAY)[2])


def test_example_template_validates_clean(capsys):
    path = SKILL / "templates" / "facts.example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    facts, dnu, findings = fc.validate_facts(data)
    assert _codes(findings) == []
    types = {f["type"] for f in facts.values()}
    assert types == set(fc.TYPES) == {"primary", "news", "estimate", "market", "own-calculation"}
    assert "_note" in data and len(dnu) == 1
    assert fc.main([str(path)]) == 0


MARKET = {"value": 41.27, "unit": "$ per share", "type": "market", "source": "Example Exchange historical prices",
          "url": "https://example.com/exchange/acme", "accessed": "2026-03-05", "as_of": "2026-03-04 close, ET"}


@pytest.mark.parametrize("as_of", [
    "2026-03-04 close, ET",
    "2026-03-04 closing auction (Example Exchange)",
    "2026-03-04 16:00 EST",
    "2026-03-04, intraday high",
    "2026-03-04 17:30 Europe/London",
    "2026-03-04 settlement, UTC+1",
    "2026-03-04T16:00:00-05:00",
    "2026-03-04 16:00 -05:00",
])
def test_market_fact_with_date_and_session_or_zone_passes(as_of):
    data = {"acme_close": dict(MARKET, as_of=as_of)}
    facts, _, findings = fc.validate_facts(data, TODAY)
    assert _codes(findings) == [] and "acme_close" in facts
    assert _codes(findings, "INFO") == ["market"]


@pytest.mark.parametrize("as_of,why", [
    (None, "need as_of"),
    ("", "need as_of"),
    ("March 4, 2026 close", "ISO date"),
    ("2026-02-30 close", "ISO date"),
    ("2026-03-04", "session"),
    ("2026-03-04 16:00", "session"),
    ("2026-06-02 close, ET", "future"),
])
def test_market_fact_needs_a_dated_session_or_zone(as_of, why):
    fact = dict(MARKET)
    if as_of is None:
        del fact["as_of"]
    else:
        fact["as_of"] = as_of
    _, _, findings = fc.validate_facts({"acme_close": fact}, TODAY)
    fails = [f for f in findings if f["level"] == "FAIL"]
    assert [f["code"] for f in fails] == ["as_of"] and why in fails[0]["message"]


def test_market_fact_still_needs_a_source_and_matches_the_script():
    fact = dict(MARKET, url="")
    assert "url" in _codes(fc.validate_facts({"acme_close": fact}, TODAY)[2])
    facts = dict(FACTS, acme_close=dict(MARKET))
    assert _status("Acme closed at $41.27 a share.", facts)[0] == ("$41.27", "sourced", "acme_close")
    assert _status("Acme closed at $41.3.", facts)[0][1] == "sourced"  # rounded to the stated precision
    assert _status("Acme closed at $41.", facts)[0][1] == "sourced"
    assert _status("Acme closed at $42.", facts)[0][1] == "unsourced"


# ---------------------------------------------------------------------------------------------------------------
# Number extraction and units
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value,kind,currency", [
    ("about 12.5bn in sales", Decimal("12.5e9"), "plain", None),
    ("it cost $1.2 million", Decimal("1.2e6"), "plain", "$"),
    ("41% of users", Decimal("41"), "percent", None),
    ("3,000 people", Decimal("3000"), "plain", None),
    ("€5bn deal", Decimal("5e9"), "plain", "€"),
    ("12,500,000,000 dollars", Decimal("12.5e9"), "plain", "$"),
    ("a £3k fee", Decimal("3000"), "plain", "£"),
    ("¥2 trillion", Decimal("2e12"), "plain", "¥"),
    ("18 percent", Decimal("18"), "percent", None),
    ("US$4 billion", Decimal("4e9"), "plain", "$"),
    ("down -3.5%", Decimal("3.5"), "percent", None),
])
def test_extract_formats(text, value, kind, currency):
    nums = fc.extract_numbers(text)
    assert len(nums) == 1
    n = nums[0]
    assert n["value"] == value and n["kind"] == kind and n["currency"] == currency


def test_extract_skips_names_and_handles_ranges():
    assert fc.extract_numbers("GPT-4 and COVID-19 and a 3D model on 5G") == []
    nums = fc.extract_numbers("between 10-15% of revenue")
    assert [(n["value"], n["kind"]) for n in nums] == [(Decimal(10), "percent"), (Decimal(15), "percent")]
    nums = fc.extract_numbers("$2 to 3 billion")
    assert [n["value"] for n in nums] == [Decimal("2e9"), Decimal("3e9")]
    assert fc.extract_numbers("a 12.5 billion figure")[0]["sign"] == 1
    assert fc.extract_numbers("fell -3%")[0]["sign"] == -1


def test_parse_unit():
    u = fc.parse_unit("billion $")
    assert u["scale"] == Decimal("1e9") and u["currency"] == "$" and u["kind"] == "plain"
    assert fc.parse_unit("USD bn")["scale"] == Decimal("1e9")
    assert fc.parse_unit("$m")["scale"] == Decimal("1e6")
    assert fc.parse_unit("%")["kind"] == "percent"
    assert fc.parse_unit("employees")["scale"] == 1
    assert fc.parse_unit("m")["scale"] == 1  # a bare "m" could be metres


# ---------------------------------------------------------------------------------------------------------------
# Script matching
# ---------------------------------------------------------------------------------------------------------------

def test_exact_match_respects_stated_precision():
    st = _status("Revenue was $12.5 billion. Exactly 12.52 billion. Or 12,520,000,000 dollars. Not 12.6bn.")
    assert st[0] == ("$12.5 billion", "sourced", "revenue_2025")  # 12.52 rounds to 12.5
    assert st[1][1] == "sourced" and st[2][1] == "sourced"
    assert st[3][1] == "unsourced"
    st = _status("It had 3,000 employees.")
    assert st[0][1] == "unsourced"  # 3,040 is not 3,000 without a hedge


def test_fact_more_precise_than_script_only_when_it_rounds():
    facts = copy.deepcopy(FACTS)
    facts["revenue_2025"]["value"] = 12.5
    assert _status("Revenue was 12.52 billion.", facts)[0][1] == "unsourced"


def _fact(value, unit):
    return {"value": value, "unit": unit, "type": "primary", "source": "Example report",
            "url": "https://example.com/report", "accessed": "2026-03-01"}


def test_rounding_more_than_five_percent_away_needs_a_hedge():
    facts = {"fee": _fact(0.45, "%")}
    assert _status("a fee of 0.45%", facts)[0][1] == "sourced"
    assert _status("a fee of 0.5%", facts)[0][1] == "unsourced"           # rounds to 0.5 but is 11 % off
    assert _status("a fee of about 0.5%", facts)[0] == ("about 0.5%", "sourced", "fee")
    assert _status("a fee of around 0.5 percent", facts)[0][1] == "sourced"
    assert _status("a fee of nearly 0.5%", facts)[0][1] == "sourced"      # 0.45 is below 0.5: true
    assert _status("a fee of under 0.5%", facts)[0][1] == "sourced"
    assert _status("a fee of about 0.4%", facts)[0][1] == "unsourced"     # 0.45 does not round to 0.4
    _, findings = _check("a fee of 0.5%", facts)
    assert "rounded, but more than 5% away" in findings[0]["message"] and "about" in findings[0]["message"]


def test_rounded_down_number_needs_a_hedge_that_points_up():
    facts = {"fee": _fact(0.54, "%")}
    assert _status("a fee of 0.5%", facts)[0][1] == "unsourced"           # 7.4 % off
    assert _status("a fee of about 0.5%", facts)[0][1] == "sourced"
    assert _status("a fee of more than 0.5%", facts)[0][1] == "sourced"
    assert _status("a fee of nearly 0.5%", facts)[0][1] == "unsourced"    # "nearly" says below 0.5; it is above
    _, findings = _check("a fee of nearly 0.5%", facts)
    assert "says the fact is below this number" in findings[0]["message"]


def test_hedged_rounding_has_a_ceiling():
    facts = {"deal": _fact(1.45, "billion $")}
    assert _status("a $1.5 billion deal", facts)[0][1] == "sourced"       # 3.4 % off: precision is enough
    assert _status("about $1 billion", facts)[0][1] == "unsourced"        # rounds to 1 but is 31 % off
    _, findings = _check("about $1 billion", facts)
    assert "write one more digit" in findings[0]["message"]
    assert _status("more than $1 billion", facts)[0][1] == "sourced"      # a bound is still true


def test_hedge_about():
    assert _status("about 3,000 employees")[0][1] == "sourced"       # 1.3 % off
    assert _status("roughly 12 billion dollars")[0][1] == "unsourced"  # 4.2 % off
    assert _status("nearly 41% share")[0] == ("nearly 41%", "sourced", "share")


def test_hedge_more_than():
    assert _status("more than 3,000 people")[0][1] == "sourced"
    assert _status("more than 3,100 people")[0][1] == "unsourced"  # fact 3,040 is not above 3,100
    assert _status("over 40% of the market")[0][1] == "sourced"
    assert _status("more than 3 people")[0][1] == "unsourced"  # a bound far from every fact sources nothing


def test_hedge_up_to():
    facts = {k: v for k, v in copy.deepcopy(FACTS).items() if k != "growth"}
    assert _status("up to 45% of buyers", facts)[0] == ("up to 45%", "sourced", "share")
    assert _status("up to 40% of buyers", facts)[0][1] == "unsourced"  # fact 41.3 is above the ceiling
    assert _status("under $13 billion")[0][1] == "sourced"
    assert _status("less than 3,040 staff")[0][1] == "sourced"  # the spec's rule: fact <= number


def test_unit_kind_and_currency_must_agree():
    assert _status("revenue of €12.5 billion")[0][1] == "unsourced"
    assert _status("41 employees")[0][1] == "unsourced"  # 41 is a percent fact, not a count


def test_placeholders():
    numbers, findings = _check("Revenue was {revenue_2025} and {missing_key}.")
    assert numbers == []
    assert [f["text"] for f in findings if f["code"] == "placeholder"] == ["{missing_key}"]


def test_do_not_use_claim():
    _, findings = _check("Acme is The Largest Widget Maker, they say.")
    hits = [f for f in findings if f["code"] == "do_not_use"]
    assert hits and hits[0]["line"] == 1 and "press release" in hits[0]["message"]
    _, findings = _check("P01 Intro.\n\nP02 Acme is the largest\nwidget maker today.")
    hits = [f for f in findings if f["code"] == "do_not_use"]
    assert hits and hits[0]["line"] == 3  # a hard-wrapped claim is still found, at the line where it starts
    _, findings = _check("> the largest widget maker (a note, not spoken)")
    assert "do_not_use" not in _codes(findings)


def test_years_are_info_and_ignore_list():
    numbers, findings = _check("In 2019 and the 1990s, sales rose 7 times by 2026.", ignore=[Decimal(7)])
    status = {n["text"]: n["status"] for n in numbers}
    assert status == {"2019": "date", "1990s": "date", "7": "ignored", "2026": "date"}
    assert _codes(findings) == [] and _codes(findings, "INFO") == ["year", "year", "year"]
    _, findings = _check("Sales rose 7 times.")
    assert _codes(findings) == ["unsourced"]


def test_dates_and_times_are_info_not_unsourced():
    script = ("On 2026-10-18 at 3:45 p.m. the CFO spoke. On Oct. 18 and on 18 October, at 11:30, "
              "revenue was $12.5 billion.")
    numbers, findings = _check(script)
    status = [(n["text"], n["status"]) for n in numbers]
    assert status == [("2026-10-18", "date"), ("3:45 p.m.", "date"), ("Oct. 18", "date"), ("18 October", "date"),
                      ("11:30", "date"), ("$12.5 billion", "sourced")]
    assert _codes(findings) == [] and set(_codes(findings, "INFO")) == {"date"}
    assert _status("Up to 40 may leave.")[0][1] == "unsourced"  # "may" the verb is not a month


def test_filing_names_are_not_numbers():
    assert _status("The 10-K and two 10-Qs say revenue was $12.5 billion.") == [
        ("$12.5 billion", "sourced", "revenue_2025")]


def test_ignore_matches_the_full_value_only():
    st = _status("A top 7 list. Revenue hit $7 billion, or 7%.", ignore=[Decimal(7)])
    assert [(t, s) for t, s, _ in st] == [("7", "ignored"), ("$7 billion", "unsourced"), ("7%", "ignored")]


def test_bom_files_read_and_non_utf8_script_is_a_usage_error(tmp_path, capsys):
    facts = tmp_path / "facts.json"
    facts.write_bytes(b"\xef\xbb\xbf" + json.dumps(FACTS).encode("utf-8"))  # Windows editors add a BOM
    script = tmp_path / "script.md"
    script.write_bytes(b"\xef\xbb\xbf---\ntitle: 99 ideas\n---\nP01 Revenue was $12.5 billion.\n")
    assert fc.main([str(facts), "--script", str(script)]) == 0
    script.write_bytes("P01 Revenue was €12.5 billion.".encode("cp1252"))
    assert fc.main([str(facts), "--script", str(script)]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_skipped_markdown_parts():
    script = "\n".join([
        "---", "title: 99", "---",
        "# Heading 2025 plan 88",
        "> note: 77 is a placeholder",
        "<!-- 66 hidden -->",
        "| P01 | 55 | table |",
        "**P01** Revenue was $12.5 billion. [[cue-44]] [ON SCREEN: 33 bars] See https://example.com/22.",
        "```", "11", "```",
    ])
    numbers, findings = _check(script)
    assert [n["text"] for n in numbers] == ["$12.5 billion"] and _codes(findings) == []


def test_main_exit_codes_and_outputs(tmp_path, capsys):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps(FACTS), encoding="utf-8")
    good = tmp_path / "good.md"
    good.write_text("P01 Acme made $12.5 billion in 2025, reportedly with about 3,000 staff.\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("P01 Acme made $13.9 billion.\n\nP02 It is the largest widget maker.\n", encoding="utf-8")
    assert fc.main([str(facts), "--script", str(good)]) == 0
    capsys.readouterr()
    out_path = tmp_path / "qa" / "facts-check.json"
    assert fc.main([str(facts), "--script", str(bad), "--out", str(out_path)]) == 1
    human = capsys.readouterr().out
    assert "UNSOURCED" in human and "L1" in human and "L3" in human and "VERDICT: FAIL" in human
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["verdict"] == "FAIL" and saved["counts"]["unsourced"] == 1
    assert fc.main([str(facts), "--script", str(bad), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["fail_count"] == 2
    assert fc.main([str(tmp_path / "nope.json")]) == 2
    assert fc.main([str(facts), "--script", str(tmp_path / "nope.md")]) == 2
    assert fc.main([str(facts), "--ignore", "ten"]) == 2
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert fc.main([str(broken)]) == 1
