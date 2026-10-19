#!/usr/bin/env python3
"""
deal_desk_intake.py - South Jersey Deal Desk intake CLI.

Writes one property into the Notion "Deals Pipeline" data source, idempotently:
an existing row with the same normalized street address is updated, never duplicated,
and an identical dossier is never appended twice.

The script does not scrape NJ Parcels, people-search sites, Zillow or Redfin. Stages 1-3
(parcel record, contacts, valuation) are researched by the operator or the agent and passed
in as flags or a --facts JSON file; anything not supplied is reported as missing, never guessed.

Environment:
  NOTION_TOKEN              Notion internal integration secret (required unless --dry-run).
  DEALS_DATA_SOURCE_ID      Deals Pipeline data source id (default below).
  DEAL_DESK_OPERATOR        Who runs the intake (e.g. Fred); recorded on the card. --operator overrides.

Requires Python 3.9+ and `requests` (not needed for --dry-run).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # --dry-run still works
    requests = None

NOTION_API = "https://api.notion.com/v1"
# 2025-09-03 addresses databases by data source id (parent + query).
NOTION_VERSION = "2025-09-03"
DATA_SOURCE_ID = os.getenv("DEALS_DATA_SOURCE_ID", "8303402e-fa35-4d41-84b5-dc48fa52376d")

STRATEGIES = ["Subject-To", "Assumable", "Lease Option", "Flip", "Rental"]
STAGES = ["1 Lead", "2 Researching", "3 Analyzing", "4 Offer out", "5 Negotiating",
          "6 Under contract", "7 Due diligence", "8 Closing", "9 Rehab",
          "10 Rented / listed", "11 Sold / holding", "Dead"]
CALLS = ["Buy", "Negotiate", "Pass", "Hold", "Rent", "Sell", "Undecided"]
CONFIDENCE = ["High", "Medium", "Low"]
LEAD_SOURCES = ["Probate", "MLS listing", "FSBO", "Tax delinquent", "Driving for dollars",
                "Referral", "Auction / sheriff", "Other"]

CASH_CAP = 10_000.0          # max cash toward the equity gap
ASSUMABLE_RATE_MAX = 3.0     # percent
ASSUMABLE_LOANS = {"FHA", "VA", "USDA"}
RICH_TEXT_LIMIT = 2000
TIMEOUT = 20
MAX_RETRIES = 4

ATTORNEY_WARNING = (
    "ATTORNEY FIRST - legal-risk flag, not legal advice. This property is listed as "
    "pre-foreclosure, lis pendens, or sheriff sale. NJ's Foreclosure Rescue Fraud Prevention Act "
    "(N.J.S.A. 46:10B-53 et seq.) may apply, including equity-purchaser and foreclosure-consultant "
    "rules. Do not contact the homeowner, pitch terms, or deliver contracts until a qualified New "
    "Jersey real estate attorney has reviewed title, the docket, and the transaction structure."
)


class IntakeError(RuntimeError):
    """Bad input or a Notion failure; main() reports it and exits 2."""


# ------------------------------------------------------------------ address helpers

_ABBR = {
    "street": "st", "st.": "st", "avenue": "ave", "ave.": "ave", "av": "ave", "drive": "dr",
    "dr.": "dr", "road": "rd", "rd.": "rd", "lane": "ln", "ln.": "ln", "court": "ct", "ct.": "ct",
    "boulevard": "blvd", "place": "pl", "terrace": "ter", "circle": "cir", "highway": "hwy",
    "north": "n", "south": "s", "east": "e", "west": "w", "n.": "n", "s.": "s", "e.": "e", "w.": "w",
}


def normalize_street(address: str) -> str:
    """'100 Example Road, Sewell NJ' -> '487 delsea dr'. Used for dedupe only."""
    street = (address or "").split(",")[0].lower()
    street = re.sub(r"[#]", " ", street)
    words = [_ABBR.get(w, w.rstrip(".")) for w in street.split()]
    return " ".join(w for w in words if w)


def search_key(address: str) -> str:
    """House number + first street word, for a broad Notion 'contains' query."""
    parts = normalize_street(address).split()
    return " ".join(parts[:2]) if len(parts) >= 2 else " ".join(parts)


# ------------------------------------------------------------------ skip-trace parsing

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_TYPE_RE = re.compile(r"\b(wireless|mobile|cell|landline|land line|voip)\b", re.I)
_CARRIER_RE = re.compile(r"\b(verizon|at&t|att|t-mobile|tmobile|sprint|cricket|metro ?pcs|boost|"
                         r"us cellular|comcast|xfinity|spectrum|optimum|google voice|bandwidth|"
                         r"twilio|onvoy|lumen|centurylink|frontier)\b", re.I)
_RECENCY_RE = re.compile(r"(?:last (?:seen|reported|updated)|reported|seen)\s*:?\s*"
                         r"([A-Za-z]{3,9}\.? \d{4}|\d{1,2}/\d{4}|\d{4}-\d{2}(?:-\d{2})?|\d{4})", re.I)
_RELATIVE_RE = re.compile(r"(?:relatives?|associates?|spouse|co-?owners?|heirs?)\s*:\s*(.+)", re.I)


def parse_skip_trace(text: str) -> Dict[str, Any]:
    """Extract CANDIDATE contacts from pasted skip-trace / people-search text.

    Results are unverified: confirm the person matches the owner and the property or mailing
    address, and scrub phones against the National Do Not Call Registry before calling/texting.
    """
    phones: List[Dict[str, Optional[str]]] = []
    seen = set()
    for line in (text or "").splitlines():
        for a, b, c in _PHONE_RE.findall(line):
            num = f"({a}) {b}-{c}"
            if num in seen:
                continue
            seen.add(num)
            ptype = _TYPE_RE.search(line)
            carrier = _CARRIER_RE.search(line)
            recency = _RECENCY_RE.search(line)
            kind = ptype.group(1).lower() if ptype else None
            if kind in ("cell", "mobile"):
                kind = "wireless"
            if kind == "land line":
                kind = "landline"
            phones.append({"number": num, "type": kind,
                           "carrier": carrier.group(1) if carrier else None,
                           "recency": recency.group(1) if recency else None})
    # wireless first, keep source order otherwise
    phones.sort(key=lambda p: 0 if p["type"] == "wireless" else 1)
    emails = list(dict.fromkeys(e.lower() for e in _EMAIL_RE.findall(text or "")))
    related: List[str] = []
    for m in _RELATIVE_RE.finditer(text or ""):
        for name in re.split(r"[;,]| and ", m.group(1)):
            name = name.strip(" .")
            if name and name not in related and not _PHONE_RE.search(name) and "@" not in name:
                related.append(name)
    return {
        "primary_phone": phones[0]["number"] if phones else None,
        "phones": phones,
        "secondary_phones": [p["number"] for p in phones[1:]],
        "emails": emails,
        "related_people": related,
        "verified": False,
        "dnc_checked": False,
    }


# ------------------------------------------------------------------ deal math

def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        raise IntakeError(f"not a number: {v!r}") from None


def assess_assumable(loan_type: Optional[str], rate: Optional[float], fixed: bool) -> Optional[bool]:
    """True for a fixed FHA/VA/USDA loan at <= 3.0%; None when loan type or rate is unknown."""
    if not loan_type or rate is None:
        return None
    return loan_type.upper() in ASSUMABLE_LOANS and fixed and rate <= ASSUMABLE_RATE_MAX


def equity_gap_plan(price: Optional[float], balance: Optional[float]) -> Optional[Dict[str, float]]:
    """Split price minus loan balance into cash (capped at CASH_CAP) and a seller carryback 2nd."""
    if price is None or balance is None:
        return None
    gap = max(price - balance, 0.0)
    cash = min(gap, CASH_CAP)
    return {"equity_gap": round(gap, 2), "cash": round(cash, 2),
            "seller_carryback_2nd": round(gap - cash, 2)}


def is_absentee(property_address: str, mailing: Optional[str]) -> Optional[bool]:
    """True when the owner mailing address differs from the property; None if unknown."""
    if not mailing:
        return None
    return normalize_street(property_address) != normalize_street(mailing)


def add_business_days(start: date, days: int) -> date:
    """Date `days` weekdays after `start` (holidays not skipped)."""
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


# ------------------------------------------------------------------ Notion payloads

def _rt(text: str) -> List[dict]:
    text = text or ""
    return [{"type": "text", "text": {"content": text[i:i + RICH_TEXT_LIMIT]}}
            for i in range(0, len(text), RICH_TEXT_LIMIT)] or [{"type": "text", "text": {"content": ""}}]


def _para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt(text)}}


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": _rt(text)}}


def build_properties(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Map deal fields to Deals Pipeline property names/types. Missing values are omitted, so an
    update never blanks a field that already has data."""
    p: Dict[str, Any] = {"Address": {"title": _rt(deal["address"])}}

    def text(name: str, key: str) -> None:
        if deal.get(key):
            p[name] = {"rich_text": _rt(str(deal[key]))}

    def number(name: str, key: str) -> None:
        if deal.get(key) is not None:
            p[name] = {"number": deal[key]}

    def select(name: str, key: str) -> None:
        if deal.get(key):
            p[name] = {"select": {"name": deal[key]}}

    def checkbox(name: str, key: str) -> None:
        if deal.get(key) is not None:
            p[name] = {"checkbox": bool(deal[key])}

    text("Town", "town")
    text("Owner name", "owner_name")
    text("Owner mailing address", "mailing_address")
    text("Block / lot", "block_lot")
    text("Property class", "property_class")
    text("Last sale", "last_sale")
    text("Beds / baths / sq ft", "specs")
    text("Loan type / rate", "loan_summary")
    text("Contact person", "contact_person")
    text("Next action", "next_action")
    text("Notes", "notes_property")
    number("Assessed value", "assessed_value")
    number("Annual taxes", "annual_taxes")
    number("ARV", "arv")
    number("Asking price", "asking_price")
    number("Equity gap", "equity_gap")
    select("Strategy", "strategy")
    select("Stage", "stage")
    select("Call", "call")
    select("Confidence", "confidence")
    select("Lead source", "lead_source")
    checkbox("Attorney first", "attorney_first")
    checkbox("Absentee owner", "absentee")
    if deal.get("owner_phone"):
        p["Owner phone"] = {"phone_number": deal["owner_phone"]}
    if deal.get("owner_email"):
        p["Owner email"] = {"email": deal["owner_email"]}
    if deal.get("next_action_date"):
        p["Next action date"] = {"date": {"start": deal["next_action_date"]}}
    return p


def build_dossier(deal: Dict[str, Any], contacts: Dict[str, Any], missing: List[str]) -> List[dict]:
    """Page blocks for one intake run: warning, stage 1-3 findings, missing list, pitch script."""
    blocks: List[dict] = []
    if deal.get("attorney_first"):
        blocks.append({"object": "block", "type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "⚠️"}, "color": "red_background",
            "rich_text": _rt(ATTORNEY_WARNING)}})
    blocks.append(_heading(f"Intake dossier {deal['run_date']} (run {deal['run_hash']})"))
    blocks.append(_para(f"Operator: {deal.get('operator') or 'not set'}. Strategy {deal.get('strategy')}, "
                        f"stage {deal.get('stage')}, call {deal.get('call')}, confidence {deal.get('confidence')}."))

    blocks.append(_heading("Stage 1 - Tax & parcel record"))
    for label, key in [("County", "county"), ("Municipality", "town"), ("APN / parcel ID", "apn"),
                       ("Block / lot", "block_lot"), ("Owner of record", "owner_name"),
                       ("Property class", "property_class"), ("Assessed value", "assessed_value"),
                       ("Net annual taxes", "annual_taxes"), ("Last sale", "last_sale"),
                       ("Owner mailing address", "mailing_address")]:
        blocks.append(_bullet(f"{label}: {deal[key] if deal.get(key) not in (None, '') else 'Not found'}"))
    if deal.get("absentee") is not None:
        blocks.append(_bullet(f"Absentee owner: {'yes' if deal['absentee'] else 'no'}"
                              f"{' (out of state)' if deal.get('out_of_state') else ''}"))

    blocks.append(_heading("Stage 2 - Contacts (UNVERIFIED, DNC not checked)"))
    if contacts["phones"]:
        for ph in contacts["phones"]:
            extra = ", ".join(x for x in [ph["type"], ph["carrier"],
                                          f"last seen {ph['recency']}" if ph["recency"] else None] if x)
            blocks.append(_bullet(f"{ph['number']}{' - ' + extra if extra else ''}"))
    else:
        blocks.append(_bullet("Phones: Not found - paste skip-trace / people-search text with --skip-text"))
    blocks.append(_bullet(f"Emails: {', '.join(contacts['emails']) or 'Not found'}"))
    blocks.append(_bullet(f"Co-owners / spouses / heirs / relatives listed: "
                          f"{', '.join(contacts['related_people']) or 'none listed'}"))
    blocks.append(_para("Confirm each person matches the owner and the property or mailing address, "
                        "and scrub phones against the National Do Not Call Registry before any call or text."))

    blocks.append(_heading("Stage 3 - Value & debt sizing"))
    for label, key in [("Specs", "specs"), ("Asking price", "asking_price"), ("Estimated value", "est_value"),
                       ("ARV (from renovated sold comps)", "arv"), ("Comps", "comps"),
                       ("Loan", "loan_summary")]:
        blocks.append(_bullet(f"{label}: {deal[key] if deal.get(key) not in (None, '') else 'Not found'}"))
    a = deal.get("assumable_ok")
    blocks.append(_bullet("Assumable <=3.0% fixed FHA/VA/USDA: " +
                          ("YES - live-in candidate (servicer must confirm)" if a else
                           "no" if a is False else "unknown - need loan type, rate, fixed/ARM")))
    plan = deal.get("gap_plan")
    if plan:
        blocks.append(_bullet(f"Equity gap ${plan['equity_gap']:,.0f}: proposed ${plan['cash']:,.0f} cash + "
                              f"${plan['seller_carryback_2nd']:,.0f} seller carryback 2nd note "
                              f"(0%-low interest). Proposal only: servicer approval + attorney-drafted note."))
    else:
        blocks.append(_bullet("Equity gap: unknown - need price and loan balance"))

    blocks.append(_heading("Still missing"))
    for m in missing or ["Nothing flagged"]:
        blocks.append(_bullet(m))
    if deal.get("notes"):
        blocks.append(_heading("Notes"))
        blocks.append(_para(deal["notes"]))

    blocks.append(_heading("Owner pitch script"))
    if deal.get("attorney_first"):
        blocks.append(_para("Pitch on hold - attorney review required before any owner contact."))
    else:
        owner = deal.get("owner_name") or "[Owner name]"
        blocks.append(_para(
            f"Hi {owner}, my name is [Your name]. I'm a local buyer and I'm interested in "
            f"{deal['address']}. If you've thought about selling, I can buy it as-is and work around your "
            f"timeline. I'd put a written offer in front of you that you can review with an attorney or "
            f"anyone you trust. No obligation. If you'd rather not hear from me, just tell me and I won't "
            f"contact you again. [Your phone / email]"))
        blocks.append(_para("Mail first. Call or text only numbers that passed a DNC scrub."))
    return blocks


def missing_fields(deal: Dict[str, Any]) -> List[str]:
    """What the operator still has to research, with where to get each item."""
    checks = [
        ("owner_name", "Owner of record - pull from njparcels.com or the county/municipal tax assessor"),
        ("block_lot", "Block / lot - njparcels.com or municipal tax map"),
        ("property_class", "Property class - tax record (MOD-IV)"),
        ("annual_taxes", "Net annual taxes - municipal tax collector / njparcels"),
        ("assessed_value", "Assessed value - county tax board / njparcels"),
        ("last_sale", "Last sale date/price - njparcels sales or county clerk deed search"),
        ("mailing_address", "Owner mailing address - tax record"),
        ("owner_phone", "Owner phone - paste skip-trace text (--skip-text)"),
        ("specs", "Beds/baths/sq ft/lot/year - listing or tax record"),
        ("arv", "ARV - renovated sold comps"),
        ("loan_summary", "Loan type/rate/balance - listing remarks, seller, or servicer"),
    ]
    out = [msg for key, msg in checks if deal.get(key) in (None, "")]
    if deal.get("attorney_first"):
        out.append("Foreclosure docket - NJ Courts public access / county sheriff sale list")
    return out


# ------------------------------------------------------------------ Notion client

def _headers() -> Dict[str, str]:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise IntakeError("NOTION_TOKEN is not set. Export your Notion integration secret first.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION}


def _request(method: str, path: str, body: Optional[dict] = None) -> dict:
    if requests is None:
        raise IntakeError("The 'requests' package is required: pip install requests")
    resp = None
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.request(method, f"{NOTION_API}{path}", headers=_headers(), json=body, timeout=TIMEOUT)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < MAX_RETRIES:
                time.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))
                continue
        if not (200 <= resp.status_code < 300):
            raise IntakeError(f"Notion {method} {path} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json()
    raise IntakeError("unreachable")


def _title_of(page: dict) -> str:
    prop = page.get("properties", {}).get("Address", {})
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))


def find_existing(address: str) -> Optional[str]:
    """Exact match on normalized street among rows whose title contains house number + street word."""
    target = normalize_street(address)
    house = target.split()[0] if target else ""
    body = {"filter": {"property": "Address", "title": {"contains": house}}, "page_size": 100}
    cursor = None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        res = _request("POST", f"/data_sources/{DATA_SOURCE_ID}/query", body)
        for page in res.get("results", []):
            title = re.sub(r"^(archived example:\s*)", "", _title_of(page), flags=re.I)
            if normalize_street(title) == target:
                return page["id"]
        if not res.get("has_more"):
            return None
        cursor = res.get("next_cursor")


def _existing_text(page_id: str) -> str:
    parts, cursor = [], None
    while True:
        q = f"?page_size=100{'&start_cursor=' + cursor if cursor else ''}"
        res = _request("GET", f"/blocks/{page_id}/children{q}")
        for b in res.get("results", []):
            for t in b.get(b.get("type", ""), {}).get("rich_text", []):
                parts.append(t.get("plain_text", ""))
        if not res.get("has_more"):
            return "\n".join(parts)
        cursor = res.get("next_cursor")


def _append(page_id: str, blocks: List[dict]) -> None:
    for i in range(0, len(blocks), 100):  # API limit: 100 children per request
        _request("PATCH", f"/blocks/{page_id}/children", {"children": blocks[i:i + 100]})


def upsert_deal(deal: Dict[str, Any], contacts: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Create the row, or update the matching one and append the dossier once per run hash."""
    props = build_properties(deal)
    missing = missing_fields(deal)
    blocks = build_dossier(deal, contacts, missing)
    if dry_run:
        return {"action": "dry-run", "properties": props, "blocks": len(blocks), "missing": missing}

    page_id = find_existing(deal["address"])
    if page_id:
        # Defaults fill new rows only; on an existing row they would overwrite real pipeline data.
        for name in deal.get("defaulted", []):
            props.pop(name, None)
        page = _request("GET", f"/pages/{page_id}")
        old_notes = "".join(t.get("plain_text", "")
                            for t in page.get("properties", {}).get("Notes", {}).get("rich_text", []))
        if "Notes" in props:
            if f"run {deal['run_hash']}" in old_notes:
                props.pop("Notes")
            elif old_notes:
                props["Notes"] = {"rich_text": _rt(deal["notes_property"] + "\n" + old_notes)}
        _request("PATCH", f"/pages/{page_id}", {"properties": props})
        if f"run {deal['run_hash']}" in _existing_text(page_id):
            return {"action": "updated", "page_id": page_id, "dossier": "unchanged (already appended)",
                    "missing": missing}
        _append(page_id, blocks)
        return {"action": "updated", "page_id": page_id, "dossier": "appended", "missing": missing}

    page = _request("POST", "/pages", {
        "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
        "properties": props, "children": blocks[:100]})
    if len(blocks) > 100:
        _append(page["id"], blocks[100:])
    return {"action": "created", "page_id": page["id"], "url": page.get("url"), "missing": missing}


# ------------------------------------------------------------------ CLI

def build_deal(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge flags over --facts, derive the computed fields, and validate select values."""
    facts: Dict[str, Any] = {}
    if args.facts:
        with open(args.facts, encoding="utf-8") as fh:
            facts = json.load(fh)

    defaulted: List[str] = []  # Notion property names filled from defaults, not from the operator

    def pick(name: str, default: Any = None, prop: Optional[str] = None) -> Any:
        v = getattr(args, name, None)
        if v not in (None, ""):
            return v
        if facts.get(name) not in (None, ""):
            return facts[name]
        if prop and default is not None:
            defaulted.append(prop)
        return default

    contacts = parse_skip_trace(pick("skip_text", "") or "")
    rate = _num(pick("rate"))
    loan_type = pick("loan_type")
    fixed = str(pick("loan_fixed", "yes")).lower() not in ("no", "false", "arm", "0")
    asking = _num(pick("asking"))
    balance = _num(pick("loan_balance"))
    plan = equity_gap_plan(asking, balance)
    mailing = pick("mailing_address")
    foreclosure = bool(args.foreclosure or facts.get("foreclosure"))

    loan_bits = [b for b in [loan_type, f"{rate}%" if rate is not None else None,
                             ("fixed" if fixed else "ARM") if loan_type else None,
                             f"balance ${balance:,.0f}" if balance is not None else None,
                             pick("loan_source")] if b]
    operator = pick("operator") or os.getenv("DEAL_DESK_OPERATOR")
    today = date.today()
    deal: Dict[str, Any] = {
        "address": args.address.strip(),
        "town": pick("town"), "county": pick("county"), "apn": pick("apn"),
        "block_lot": pick("block_lot"), "owner_name": pick("owner_name"),
        "property_class": pick("property_class"), "assessed_value": _num(pick("assessed_value")),
        "annual_taxes": _num(pick("annual_taxes")), "last_sale": pick("last_sale"),
        "mailing_address": mailing, "absentee": is_absentee(args.address, mailing),
        "out_of_state": bool(mailing and not re.search(r"\b(NJ|New Jersey)\b", mailing, re.I)),
        "specs": pick("specs"), "asking_price": asking, "est_value": pick("est_value"),
        "arv": _num(pick("arv")), "comps": pick("comps"),
        "loan_summary": ", ".join(loan_bits) or None,
        "assumable_ok": assess_assumable(loan_type, rate, fixed),
        "gap_plan": plan, "equity_gap": plan["equity_gap"] if plan else None,
        "strategy": pick("strategy"), "stage": pick("stage", "2 Researching", "Stage"),
        "call": pick("call", "Undecided", "Call"), "confidence": pick("confidence", "Low", "Confidence"),
        "lead_source": pick("lead_source", "Auction / sheriff" if foreclosure else None, "Lead source"),
        # True or None: an intake never clears an Attorney first flag someone already set.
        "attorney_first": True if foreclosure else None,
        "owner_phone": contacts["primary_phone"], "owner_email": (contacts["emails"] or [None])[0],
        "operator": operator, "notes": pick("notes"),
        "run_date": today.isoformat(),
    }
    days = int(pick("followup_days", 2, "Next action date"))
    deal["next_action_date"] = add_business_days(today, days).isoformat()
    deal["next_action"] = pick("next_action", (
        "ATTORNEY FIRST: verify docket and get NJ attorney review before any owner contact."
        if foreclosure else "Fill missing tax-record and contact fields; verify loan with seller/servicer."),
        "Next action")
    if foreclosure:  # a foreclosure flag must always reach the card, even on an existing row
        for name in ("Next action", "Next action date"):
            if name in defaulted:
                defaulted.remove(name)
    if deal["strategy"] is None:
        deal["strategy"] = "Assumable" if deal["assumable_ok"] else "Subject-To"
        if not deal["assumable_ok"]:
            defaulted.append("Strategy")
    if deal["strategy"] not in STRATEGIES:
        raise IntakeError(f"strategy must be one of {STRATEGIES}")
    for key, allowed in [("stage", STAGES), ("call", CALLS), ("confidence", CONFIDENCE)]:
        if deal[key] not in allowed:
            raise IntakeError(f"{key} must be one of {allowed}")
    if deal["lead_source"] and deal["lead_source"] not in LEAD_SOURCES:
        raise IntakeError(f"lead_source must be one of {LEAD_SOURCES}")

    summary = [f"[intake {deal['run_date']}{' by ' + operator if operator else ''}, run {{hash}}]"]
    if deal["assumable_ok"]:
        summary.append("Assumable <=3% - live-in candidate (unverified until servicer confirms).")
    if contacts["primary_phone"]:
        summary.append("Contacts from pasted skip-trace: UNVERIFIED, DNC not checked.")
    if deal["notes"]:
        summary.append(deal["notes"])
    deal["notes_property"] = " ".join(summary)
    if contacts["related_people"]:
        deal["contact_person"] = "Listed related people (unverified): " + ", ".join(contacts["related_people"])

    # Same facts -> same hash on any day, so a rerun never appends a duplicate dossier.
    volatile = ("run_date", "next_action_date", "notes_property")
    fingerprint = json.dumps({k: v for k, v in deal.items() if k not in volatile},
                             sort_keys=True, default=str) + json.dumps(contacts, sort_keys=True)
    deal["run_hash"] = hashlib.sha256(fingerprint.encode()).hexdigest()[:10]
    deal["notes_property"] = deal["notes_property"].replace("{hash}", deal["run_hash"])
    deal["defaulted"] = defaulted
    return deal, contacts


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """CLI definition; --skip-file contents are appended to --skip-text."""
    ap = argparse.ArgumentParser(
        prog="deal-desk-intake",
        description="Add or update a South Jersey property in the Notion Deals Pipeline (idempotent).")
    ap.add_argument("--address", required=True, help='Street address, e.g. "100 Example Rd, Sewell, NJ 08080"')
    ap.add_argument("--town", help='Municipality and county, e.g. "Washington Twp, Gloucester County"')
    ap.add_argument("--strategy", choices=STRATEGIES,
                    help="Default: Assumable if the loan qualifies, else Subject-To")
    ap.add_argument("--foreclosure", action="store_true",
                    help="Pre-foreclosure / lis pendens / sheriff sale: sets Attorney first and holds the pitch")
    ap.add_argument("--skip-text", dest="skip_text", default="",
                    help="Raw text pasted from a skip-trace or people-search result")
    ap.add_argument("--skip-file", help="File containing skip-trace text (alternative to --skip-text)")
    ap.add_argument("--facts", help="JSON file with any of the fact fields below (flags win)")
    ap.add_argument("--operator", help="Who is running the intake (default: $DEAL_DESK_OPERATOR)")
    g = ap.add_argument_group("Stage 1 - tax & parcel")
    for f in ["county", "apn", "block_lot", "owner_name", "property_class", "assessed_value",
              "annual_taxes", "last_sale", "mailing_address"]:
        g.add_argument("--" + f.replace("_", "-"), dest=f)
    g = ap.add_argument_group("Stage 3 - value & loan")
    for f in ["specs", "asking", "est_value", "arv", "comps", "loan_type", "rate", "loan_fixed",
              "loan_balance", "loan_source"]:
        g.add_argument("--" + f.replace("_", "-"), dest=f)
    g = ap.add_argument_group("Pipeline")
    g.add_argument("--stage", choices=STAGES)
    g.add_argument("--call", choices=CALLS)
    g.add_argument("--confidence", choices=CONFIDENCE)
    g.add_argument("--lead-source", dest="lead_source", choices=LEAD_SOURCES)
    g.add_argument("--next-action", dest="next_action")
    g.add_argument("--followup-days", dest="followup_days", type=int)
    g.add_argument("--notes")
    ap.add_argument("--dry-run", action="store_true", help="Print the Notion payload; write nothing")
    args = ap.parse_args(argv)
    if args.skip_file:
        with open(args.skip_file, encoding="utf-8") as fh:
            args.skip_text = (args.skip_text + "\n" + fh.read()).strip()
    return args


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point: prints the JSON result, returns 0 on success and 2 on IntakeError."""
    try:
        args = parse_args(argv)
        deal, contacts = build_deal(args)
        result = upsert_deal(deal, contacts, dry_run=args.dry_run)
    except IntakeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
