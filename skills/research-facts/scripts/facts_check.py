"""Every number has a source: validate facts.json, then check that every number in a script comes from it.

Why: corrections on a factual channel almost always start with a number that nobody can trace. One fact pack
(facts.json) holds every number with its source; this script refuses a pack with holes and a script that says a
number the pack does not hold.

facts.json is an object `key -> fact`:
  {"value": 12.5, "unit": "billion $", "type": "primary" | "news" | "estimate" | "own-calculation",
   "source": "...", "url": "https://...", "accessed": "YYYY-MM-DD", "as_of"?: "...", "quote"?: "...",
   "calc"?: "key_a / key_b * 100"}
Keys starting with "_" are ignored. An optional top-level "do_not_use": [{"claim": "...", "why": "..."}] lists
things that must not be said.

Pack checks (FAIL unless noted): numeric value; known type; source; http(s) url (not required for
own-calculation); `accessed` an ISO date, not in the future; own-calculation has `calc`, every name in it is an
existing key, and when `calc` is plain arithmetic (numbers, keys, + - * / and parentheses) it recomputes to
`value` within 1 %. `news` and `estimate` facts get an INFO reminder about how to say them.

Script checks (`--script`): headings, `>` notes, HTML comments, tables, `[[cue]]` anchors and bracketed stage
directions are skipped. `{key}` placeholders must exist. Every other digit-based number ("$12.5 billion",
"12.5bn", "41%", "3,000", "12,500,000,000") is normalized with its scale and matched against the facts, using each
fact's unit text for scale, currency and percent:
  - no hedge: the fact rounded to the stated precision must equal the number;
  - about / around / roughly / nearly / almost / approximately / close to: within 3 % of the fact;
  - more than / over / above (at least): the fact is above (or equal to) the number;
  - up to / under / below / less than / fewer than (at most): the fact is at or below the number.
  A bound more than 2x away from the fact is not sourced by it ("more than 3" does not cite 3 billion).
Years 1900-2100 and decades ("1990s") without another marker are INFO (dates, not checked). `--ignore 10,2026`
skips listed numbers. An unmatched number is UNSOURCED (FAIL); a do_not_use claim found in the script is a FAIL.

Run:
  python facts_check.py facts.json
  python facts_check.py facts.json --script script.md --ignore 10 --out qa/facts-check.json
  python facts_check.py facts.json --script script.md --json

Exit codes: 0 pass, 1 any FAIL, 2 usage error (missing file, bad option).
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older or redirected streams
        pass

TYPES = ("primary", "news", "estimate", "own-calculation")
CALC_TOLERANCE = 0.01
APPROX_TOLERANCE = Decimal("0.03")
BOUND_FACTOR = Decimal(2)
TYPE_INFO = {"news": "news source; say it with 'reportedly' (or name the outlet)",
             "estimate": "estimate; say it with 'estimated'"}

SCALE_WORDS = {"thousand": 3, "k": 3, "million": 6, "mn": 6, "m": 6, "mm": 6, "billion": 9, "bn": 9, "b": 9,
               "trillion": 12, "tn": 12, "t": 12}
SINGLE_LETTER_SCALES = {"k", "m", "b", "t"}
CURRENCY_WORDS = {"$": "$", "usd": "$", "dollar": "$", "dollars": "$", "€": "€", "eur": "€", "euro": "€",
                  "euros": "€", "£": "£", "gbp": "£", "pound": "£", "pounds": "£", "¥": "¥", "jpy": "¥",
                  "yen": "¥"}
PERCENT_WORDS = {"%", "percent", "pct", "percentage", "pp", "per cent"}

NUM_RE = re.compile(
    r"(?<![\w.,])(?<![^\W\d_][-‑])"
    r"(?P<sign>[-+−])?"
    r"(?P<cur>[$€£¥])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:(?P<suffix>bn|mn|tn|k|m|b|t)(?![^\W_])"
    r"|\s?(?P<pct>%)"
    r"|\s(?P<pctword>percentage points?|percent|per cent)\b"
    r"|\s(?P<word>thousand|million|billion|trillion|bn|mn|tn)\b)?",
    re.I)
HEDGE_RE = re.compile(
    r"\b(?P<h>approximately|roughly|around|about|nearly|almost|close to|no more than|not more than|more than|"
    r"at least|at most|over|above|up to|under|below|less than|fewer than)\s+$", re.I)
HEDGE_KIND = {"approximately": "approx", "roughly": "approx", "around": "approx", "about": "approx",
              "nearly": "approx", "almost": "approx", "close to": "approx",
              "more than": "above", "over": "above", "above": "above", "at least": "at_least",
              "up to": "below", "under": "below", "below": "below", "less than": "below", "fewer than": "below",
              "at most": "below", "no more than": "below", "not more than": "below"}
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][\w.\-]*)\}")
PARA_ID_RE = re.compile(r"^\s*(?:\*\*|__)?P\d+(?:\*\*|__)?[.:)]?\s+")


class UsageError(Exception):
    """Bad input or a broken environment (exit 2)."""


def _finding(level: str, code: str, message: str, **extra) -> dict:
    out = {"level": level, "code": code, "message": message}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (float("inf"),
                                                                                             float("-inf"))


def _dec(x) -> Decimal:
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


# ---------------------------------------------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------------------------------------------

def parse_unit(unit: str) -> dict:
    """Read scale, currency and percent from a fact's unit text ("billion $", "USD bn", "%", "employees")."""
    text = (unit or "").lower().replace("per cent", "percent")
    tokens = re.findall(r"[$€£¥%]|[^\W\d_]+", text)
    currency = next((CURRENCY_WORDS[t] for t in tokens if t in CURRENCY_WORDS), None)
    percent = any(t in PERCENT_WORDS for t in tokens)
    exp = 0
    for t in tokens:
        if t in SCALE_WORDS and (t not in SINGLE_LETTER_SCALES or currency):
            exp = SCALE_WORDS[t]
            break
    if not exp:  # "$bn", "bn$", "$12m" style units glue the symbol to the scale
        m = re.search(r"[$€£¥](bn|mn|tn|k|m|b|t)\b|\b(bn|mn|tn|k|m|b|t)[$€£¥]", text)
        if m:
            exp = SCALE_WORDS[m.group(1) or m.group(2)]
    return {"scale": Decimal(10) ** exp, "currency": currency, "kind": "percent" if percent else "plain"}


# ---------------------------------------------------------------------------------------------------------------
# facts.json validation
# ---------------------------------------------------------------------------------------------------------------

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult,
                  ast.Div, ast.UAdd, ast.USub, ast.Constant)


def calc_names(calc: str) -> tuple[set[str], bool]:
    """Names used by a calc string, and whether it is plain arithmetic that can be recomputed."""
    try:
        tree = ast.parse(calc.strip(), mode="eval")
    except SyntaxError:
        tree = None
    if tree is not None:
        func_names = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in func_names}
        arithmetic = all(isinstance(n, _ALLOWED_NODES) for n in ast.walk(tree)) and all(
            _is_number(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant))
        return names, arithmetic
    braced = set(PLACEHOLDER_RE.findall(calc))
    bare = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", calc) if "_" in t}
    return braced | bare, False


def eval_calc(calc: str, values: dict) -> float:
    """Safely evaluate plain arithmetic over fact keys. Raises ValueError or ZeroDivisionError."""
    tree = ast.parse(calc.strip(), mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and _is_number(node.value):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ValueError(f"unknown key {node.id!r}")
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            return a / b
        raise ValueError("not plain arithmetic")

    return ev(tree)


def _valid_iso_date(s) -> dt.date | None:
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.strip()):
        return None
    try:
        return dt.date.fromisoformat(s.strip())
    except ValueError:
        return None


def validate_facts(data, today: dt.date | None = None) -> tuple[dict, list, list[dict]]:
    """Return (facts, do_not_use, findings). facts holds every entry whose value is a number."""
    today = today or dt.date.today()
    findings: list[dict] = []
    if not isinstance(data, dict):
        return {}, [], [_finding("FAIL", "schema", "facts.json must be a JSON object: key -> fact")]

    do_not_use = []
    raw_dnu = data.get("do_not_use", [])
    if not isinstance(raw_dnu, list):
        findings.append(_finding("FAIL", "schema", "do_not_use must be a list of {claim, why}", key="do_not_use"))
    else:
        for i, item in enumerate(raw_dnu):
            if not isinstance(item, dict) or not isinstance(item.get("claim"), str) or not item["claim"].strip():
                findings.append(_finding("FAIL", "schema", f"do_not_use[{i}] needs a non-empty 'claim'",
                                         key="do_not_use"))
                continue
            if not isinstance(item.get("why"), str) or not item["why"].strip():
                findings.append(_finding("FAIL", "schema", f"do_not_use[{i}] needs a 'why'", key="do_not_use"))
            do_not_use.append({"claim": item["claim"].strip(), "why": str(item.get("why") or "").strip()})

    entries = {k: v for k, v in data.items() if not str(k).startswith("_") and k != "do_not_use"}
    facts: dict = {}
    for key, e in entries.items():
        if not isinstance(e, dict):
            findings.append(_finding("FAIL", "schema", "fact must be an object", key=key))
            continue
        if _is_number(e.get("value")):
            facts[key] = e
        else:
            findings.append(_finding("FAIL", "value", f"value must be a number, got {e.get('value')!r}", key=key))
        if not isinstance(e.get("unit"), str):
            findings.append(_finding("FAIL", "unit", "unit must be a string (use \"\" for a plain count)", key=key))
        typ = e.get("type")
        if typ not in TYPES:
            findings.append(_finding("FAIL", "type", f"type must be one of {', '.join(TYPES)}; got {typ!r}",
                                     key=key))
        own = typ == "own-calculation"
        if not own and not (isinstance(e.get("source"), str) and e["source"].strip()):
            findings.append(_finding("FAIL", "source", "source is missing", key=key))
        url = e.get("url")
        if not own or url not in (None, ""):
            if not (isinstance(url, str) and re.match(r"https?://[^\s/]+\.[^\s]+", url.strip(), re.I)):
                findings.append(_finding("FAIL", "url", f"url must be an http(s) link, got {url!r}", key=key))
        accessed = e.get("accessed")
        if not own or accessed not in (None, ""):
            d = _valid_iso_date(accessed)
            if d is None:
                findings.append(_finding("FAIL", "accessed", f"accessed must be YYYY-MM-DD, got {accessed!r}",
                                         key=key))
            elif d > today:
                findings.append(_finding("FAIL", "accessed", f"accessed {accessed} is in the future", key=key))
        for opt in ("as_of", "quote", "calc"):
            if opt in e and not isinstance(e[opt], str):
                findings.append(_finding("FAIL", "schema", f"{opt} must be a string", key=key))
        if typ in TYPE_INFO:
            findings.append(_finding("INFO", typ, TYPE_INFO[typ], key=key))

    for key, e in entries.items():
        if not isinstance(e, dict) or e.get("type") != "own-calculation":
            continue
        calc = e.get("calc")
        if not isinstance(calc, str) or not calc.strip():
            findings.append(_finding("FAIL", "calc", "own-calculation needs a 'calc' formula over other keys",
                                     key=key))
            continue
        names, arithmetic = calc_names(calc)
        missing = sorted(n for n in names if n not in entries)
        if key in names:
            findings.append(_finding("FAIL", "calc", "calc refers to its own key", key=key))
        if missing:
            findings.append(_finding("FAIL", "calc", "calc names unknown keys: " + ", ".join(missing), key=key))
        if not (names & set(entries)) - {key}:
            findings.append(_finding("FAIL", "calc", "calc must use other facts keys, so its inputs are sourced",
                                     key=key))
        if missing or key in names or key not in facts:
            continue
        if not arithmetic:
            findings.append(_finding("INFO", "calc", "calc is not plain arithmetic over keys; recompute it by hand",
                                     key=key))
            continue
        if any(n not in facts for n in names):
            findings.append(_finding("INFO", "calc", "calc not recomputed: an input has no numeric value", key=key))
            continue
        try:
            result = eval_calc(calc, {k: facts[k]["value"] for k in names})
        except ZeroDivisionError:
            findings.append(_finding("FAIL", "calc", "calc divides by zero", key=key))
            continue
        except ValueError as exc:
            findings.append(_finding("FAIL", "calc", f"calc cannot be evaluated: {exc}", key=key))
            continue
        value = float(facts[key]["value"])
        ok = abs(result - value) <= CALC_TOLERANCE * abs(value) if value else abs(result) < 1e-9
        if not ok:
            findings.append(_finding("FAIL", "calc", f"calc gives {result:.6g}, value says {value:.6g} "
                                                     f"(more than {CALC_TOLERANCE:.0%} apart)", key=key))
    return facts, do_not_use, findings


# ---------------------------------------------------------------------------------------------------------------
# Script reading
# ---------------------------------------------------------------------------------------------------------------

def spoken_lines(text: str) -> list[tuple[int, str, list[str]]]:
    """(line number, cleaned spoken text, placeholder keys) for every line that is spoken."""
    out = []
    lines = text.splitlines()
    in_comment = in_fence = in_front = False
    for i, raw in enumerate(lines, start=1):
        line = raw
        if i == 1 and line.strip() == "---":
            in_front = True
            continue
        if in_front:
            if line.strip() in ("---", "..."):
                in_front = False
            continue
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        cleaned = ""
        rest = line
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    rest, in_comment = rest[end + 3:], False
                continue
            start = rest.find("<!--")
            if start < 0:
                cleaned += rest
                rest = ""
            else:
                cleaned += rest[:start] + " "
                rest, in_comment = rest[start + 4:], True
        s = cleaned.strip()
        if not s or s.startswith(("#", ">", "|")) or re.fullmatch(r"[-*_=\s]{3,}", s):
            continue
        s = re.sub(r"\[\[[^\]]*\]\]", " ", s)
        s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"\[[^\]]*\]", " ", s)
        s = re.sub(r"https?://\S+", " ", s)
        s = PARA_ID_RE.sub("", s)
        keys = PLACEHOLDER_RE.findall(s)
        s = PLACEHOLDER_RE.sub(" ", s)
        s = re.sub(r"\*\*|__|~~|`", "", s)
        s = re.sub(r"(?<!\w)[*_]|[*_](?!\w)", "", s)
        s = s.replace("’", "'").replace("‘", "'").replace(" ", " ")
        s = re.sub(r"\s+", " ", s).strip()
        if s or keys:
            out.append((i, s, keys))
    return out


def extract_numbers(line: str) -> list[dict]:
    """Every digit-based number in one cleaned line, with scale, kind, currency and the hedge before it."""
    found = []
    for m in NUM_RE.finditer(line):
        num_txt = m.group("num")
        suffix = (m.group("suffix") or m.group("word") or "").lower()
        percent = bool(m.group("pct") or m.group("pctword"))
        rest = line[m.end():]
        ordinal = decade = multiplier = False
        if not suffix and not percent:
            if re.match(r"(?:st|nd|rd|th)\b", rest, re.I):
                ordinal = True
            elif len(num_txt) == 4 and re.match(r"'?s\b", rest):
                decade = True
            elif re.match(r"x\b", rest, re.I):
                multiplier = True
            elif re.match(r"[^\W_]", rest):
                continue  # part of a name such as 3D, 5G, A4
        cur = m.group("cur")
        prefix = line[:m.start()]
        if not cur and prefix.endswith(("$", "€", "£", "¥")):
            cur, prefix = prefix[-1], prefix[:-1]
        mcode = re.search(r"\b(US|AU|CA|HK|NZ|A|C|USD|EUR|GBP|JPY)\s?$", prefix)
        if mcode and (cur or mcode.group(1) in ("USD", "EUR", "GBP", "JPY")):
            if not cur:
                cur = CURRENCY_WORDS[mcode.group(1).lower()]
            prefix = prefix[:mcode.start()]
        if not cur:
            after = re.match(r"\s*(dollars?|usd|euros?|eur|pounds?|gbp|yen|jpy)\b", rest, re.I)
            if after:
                cur = CURRENCY_WORDS[after.group(1).lower()]
        clean = num_txt.replace(",", "")
        mantissa = Decimal(clean)
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        hm = HEDGE_RE.search(prefix)
        tail = re.match(r"(?:st|nd|rd|th|'?s|x)\b", rest, re.I).group(0) if (ordinal or decade or multiplier) else ""
        found.append({
            "text": m.group(0).strip() + tail, "start": m.start(), "end": m.end(),
            "sign": -1 if m.group("sign") in ("-", "−") else 1,
            "currency": cur, "mantissa": mantissa, "decimals": decimals,
            "scale": Decimal(10) ** SCALE_WORDS.get(suffix, 0),
            "kind": "percent" if percent else "plain",
            "has_comma": "," in num_txt, "ordinal": ordinal, "decade": decade, "multiplier": multiplier,
            "hedge": hm.group("h").lower() if hm else None,
            "has_scale": bool(suffix),
        })
    # ranges: "10-15%", "$10 to 15 billion", "between 2 and 3 million" share the marker of the other end
    for a, b in zip(found, found[1:]):
        gap = line[a["end"]:b["start"]]
        joined = re.fullmatch(r"\s*(?:-|–|—|to)\s*", gap, re.I) or (
            re.fullmatch(r"\s*and\s*", gap, re.I) and re.search(r"\bbetween\s+\S*$", line[:a["start"]], re.I))
        if not joined:
            continue
        if not a["has_scale"] and a["kind"] == "plain" and (b["has_scale"] or b["kind"] == "percent"):
            a["scale"], a["kind"], a["has_scale"] = b["scale"], b["kind"], b["has_scale"]
        a["currency"] = a["currency"] or b["currency"]
        b["currency"] = b["currency"] or a["currency"]
        if a["hedge"] and not b["hedge"]:
            b["hedge"] = a["hedge"]
    for n in found:
        n["value"] = n["mantissa"] * n["scale"]
    return found


def _is_year_like(n: dict) -> bool:
    if n["decade"]:
        return True
    plain = (n["kind"] == "plain" and not n["currency"] and not n["has_scale"] and not n["has_comma"]
             and n["decimals"] == 0 and n["sign"] == 1 and not n["ordinal"] and not n["multiplier"])
    return plain and 1900 <= n["mantissa"] <= 2100


def _fact_table(facts: dict) -> list[dict]:
    table = []
    for key, f in facts.items():
        u = parse_unit(f.get("unit") if isinstance(f.get("unit"), str) else "")
        table.append({"key": key, "abs": abs(_dec(f["value"])) * u["scale"], "kind": u["kind"],
                      "currency": u["currency"], "value": f["value"], "unit": f.get("unit", "")})
    return table


def _matches(n: dict, fact_abs: Decimal) -> bool:
    num_abs = abs(n["value"])
    kind = HEDGE_KIND.get(n["hedge"] or "")
    if kind == "approx":
        if fact_abs == 0:
            return num_abs == 0
        return abs(fact_abs - num_abs) <= APPROX_TOLERANCE * fact_abs
    if kind in ("above", "at_least"):
        ok = fact_abs > num_abs if kind == "above" else fact_abs >= num_abs
        return ok and fact_abs <= num_abs * BOUND_FACTOR
    if kind == "below":
        return fact_abs <= num_abs and fact_abs * BOUND_FACTOR >= num_abs
    try:
        q = Decimal(1).scaleb(-n["decimals"])
        return (fact_abs / n["scale"]).quantize(q, rounding=ROUND_HALF_UP) == n["mantissa"]
    except InvalidOperation:
        return False


def match_number(n: dict, table: list[dict]) -> tuple[dict | None, dict | None]:
    """(matching fact row, closest fact row of the same kind)."""
    same_kind = [f for f in table if f["kind"] == n["kind"]
                 and not (n["currency"] and f["currency"] and n["currency"] != f["currency"])]
    num_abs = abs(n["value"])

    def distance(f):
        base = max(f["abs"], num_abs)
        return abs(f["abs"] - num_abs) / base if base else Decimal(0)

    ranked = sorted(same_kind, key=distance)
    for f in ranked:
        if _matches(n, f["abs"]):
            return f, f
    return None, (ranked[0] if ranked else None)


def check_script(text: str, facts: dict, do_not_use: list, ignore: list[Decimal] | None = None,
                 all_keys: set | None = None) -> tuple[list[dict], list[dict]]:
    """Return (numbers with status, findings) for one script."""
    ignore = ignore or []
    all_keys = set(facts) if all_keys is None else all_keys
    table = _fact_table(facts)
    numbers: list[dict] = []
    findings: list[dict] = []
    lines = spoken_lines(text)
    for lineno, s, keys in lines:
        for k in keys:
            if k not in all_keys:
                findings.append(_finding("FAIL", "placeholder", f"{{{k}}} is not a key in facts.json", line=lineno,
                                         text="{" + k + "}"))
        for n in extract_numbers(s):
            shown = (f"{n['hedge']} " if n["hedge"] else "") + n["text"]
            row = {"line": lineno, "text": shown, "value": float(n["value"]) * n["sign"], "kind": n["kind"],
                   "currency": n["currency"], "hedge": n["hedge"], "status": None, "key": None}
            if any(n["mantissa"] == v or abs(n["value"]) == v for v in ignore):
                row["status"] = "ignored"
                numbers.append(row)
                continue
            fact, closest = match_number(n, table)
            if fact:
                row["status"], row["key"] = "sourced", fact["key"]
            elif _is_year_like(n):
                row["status"] = "date"
                findings.append(_finding("INFO", "year", f"\"{n['text']}\" looks like a year; dates are not checked",
                                         line=lineno, text=n["text"]))
            else:
                row["status"] = "unsourced"
                msg = f"\"{shown}\": no fact matches"
                if closest:
                    base = max(closest["abs"], abs(n["value"]))
                    off = float(abs(closest["abs"] - abs(n["value"])) / base * 100) if base else 0.0
                    msg += f"; closest is {closest['key']} = {closest['value']:g} {closest['unit']} ({off:.1f}% off)"
                    row["closest"] = closest["key"]
                findings.append(_finding("FAIL", "unsourced", msg, line=lineno, text=shown))
            numbers.append(row)
    joined = "\n".join(s for _, s, _ in lines)
    line_of = [lineno for lineno, _, _ in lines]
    for item in do_not_use:
        words = item["claim"].replace("’", "'").split()
        if not words:
            continue
        pattern = r"\s+".join(re.escape(w) for w in words)
        for m in re.finditer(pattern, joined, re.I):
            lineno = line_of[joined[:m.start()].count("\n")] if line_of else None
            why = f" ({item['why']})" if item["why"] else ""
            findings.append(_finding("FAIL", "do_not_use", f"do-not-use claim \"{item['claim']}\"{why}",
                                     line=lineno, text=m.group(0)))
    findings.sort(key=lambda f: (f.get("line") or 0))
    return numbers, findings


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def parse_ignore(text: str | None) -> list[Decimal]:
    if not text:
        return []
    out = []
    for part in text.split(","):
        part = part.strip().replace("_", "")
        if not part:
            continue
        try:
            out.append(Decimal(part))
        except InvalidOperation:
            raise UsageError(f"--ignore expects numbers separated by commas, got {part!r}") from None
    return out


def run(facts_path: Path, script_path: Path | None, ignore: list[Decimal], today: dt.date | None = None) -> dict:
    try:
        raw = facts_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise UsageError(f"facts file not found: {facts_path}") from None
    except OSError as exc:
        raise UsageError(f"cannot read {facts_path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        data = None
        pack_findings = [_finding("FAIL", "json", f"facts.json is not valid JSON: {exc}")]
        facts, dnu = {}, []
    else:
        facts, dnu, pack_findings = validate_facts(data, today)
    result = {"facts_file": str(facts_path), "facts": len(facts), "do_not_use": len(dnu),
              "pack_findings": pack_findings}
    if script_path is not None:
        try:
            text = script_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise UsageError(f"script not found: {script_path}") from None
        except OSError as exc:
            raise UsageError(f"cannot read {script_path}: {exc}") from exc
        all_keys = {k for k in data if not str(k).startswith("_")} if isinstance(data, dict) else set()
        numbers, script_findings = check_script(text, facts, dnu, ignore, all_keys)
        result.update({"script_file": str(script_path), "numbers": numbers, "script_findings": script_findings})
        counts = {}
        for n in numbers:
            counts[n["status"]] = counts.get(n["status"], 0) + 1
        result["counts"] = counts
    fails = [f for f in pack_findings + result.get("script_findings", []) if f["level"] == "FAIL"]
    result["fail_count"] = len(fails)
    result["verdict"] = "FAIL" if fails else "PASS"
    return result


def print_human(r: dict) -> None:
    print(f"{r['facts_file']}: {r['facts']} facts, {r['do_not_use']} do-not-use claim"
          f"{'s' if r['do_not_use'] != 1 else ''}")
    order = {"FAIL": 0, "INFO": 1}
    for f in sorted(r["pack_findings"], key=lambda f: order.get(f["level"], 2)):
        where = f.get("key") or "-"
        print(f"  {f['level']:<9} {where}: {f['message']}")
    if "script_file" in r:
        c = r["counts"]
        total = sum(c.values())
        print(f"{r['script_file']}: {total} number{'s' if total != 1 else ''}: {c.get('sourced', 0)} sourced, "
              f"{c.get('date', 0)} dates, {c.get('ignored', 0)} ignored, {c.get('unsourced', 0)} unsourced")
        for f in sorted(r["script_findings"], key=lambda f: (order.get(f["level"], 2), f.get("line") or 0)):
            label = "UNSOURCED" if f["code"] == "unsourced" else f["level"]
            print(f"  L{f.get('line', '?'):<4} {label:<9} {f['message']}")
        sourced = [n for n in r["numbers"] if n["status"] == "sourced"]
        if sourced:
            print("Sourced")
            for n in sourced:
                print(f"  L{n['line']:<4} \"{n['text']}\" -> {n['key']}")
    print(f"VERDICT: {r['verdict']}" + (f" ({r['fail_count']} problem{'s' if r['fail_count'] != 1 else ''})"
                                         if r["fail_count"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("facts", help="path to facts.json")
    ap.add_argument("--script", help="also check every number in this script")
    ap.add_argument("--ignore", help="numbers to skip in the script, comma-separated (e.g. 10,2026)")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    ap.add_argument("--out", help="also write the JSON result to this file")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    try:
        ignore = parse_ignore(args.ignore)
        result = run(Path(args.facts), Path(args.script) if args.script else None, ignore)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.out:
        try:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
