#!/usr/bin/env python3
"""South Jersey Deal Desk intake CLI: idempotently upsert one property into the Notion Deals Pipeline."""
# An existing row with the same normalized street address is updated, never duplicated, and an
# identical dossier is never appended twice.
#
# The script does not scrape NJ Parcels, people-search sites, Zillow or Redfin. Stages 1-3
# (parcel record, contacts, valuation) are researched by the operator or the agent and passed
# in as flags or a --facts JSON file; anything not supplied is reported as missing, never guessed.
#
# Environment:
#   NOTION_TOKEN              Notion internal integration secret (required unless --dry-run).
#   DEALS_DATA_SOURCE_ID      Deals Pipeline data source id (default below).
#   DEAL_DESK_OPERATOR        Who runs the intake (e.g. Fred); recorded on the card. --operator overrides.
#
# Requires Python 3.9+ and `requests` (not needed for --dry-run).

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from deal_desk_dossier import build_dossier, build_properties, missing_fields, rich_text as _rt

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
TIMEOUT = 20
MAX_RETRIES = 4

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


_KIND_ALIASES = {"cell": "wireless", "mobile": "wireless", "land line": "landline"}


def _group(match: Optional[re.Match]) -> Optional[str]:
    return match.group(1) if match else None


def _phones_in_line(line: str, seen: set) -> List[Dict[str, Optional[str]]]:
    """Phones on one line, tagged with the type/carrier/recency found on that line."""
    kind = (_group(_TYPE_RE.search(line)) or "").lower()
    found = []
    for a, b, c in _PHONE_RE.findall(line):
        num = f"({a}) {b}-{c}"
        if num not in seen:
            seen.add(num)
            found.append({"number": num, "type": _KIND_ALIASES.get(kind, kind) or None,
                          "carrier": _group(_CARRIER_RE.search(line)),
                          "recency": _group(_RECENCY_RE.search(line))})
    return found


def _is_person_name(name: str) -> bool:
    return bool(name) and "@" not in name and not _PHONE_RE.search(name)


def _related_people(text: str) -> List[str]:
    related: List[str] = []
    for m in _RELATIVE_RE.finditer(text):
        for name in re.split(r"[;,]| and ", m.group(1)):
            name = name.strip(" .")
            if _is_person_name(name) and name not in related:
                related.append(name)
    return related


def parse_skip_trace(text: str) -> Dict[str, Any]:
    """Extract CANDIDATE contacts from pasted skip-trace / people-search text."""
    # Results are unverified: confirm the person matches the owner and the property or mailing
    # address, and scrub phones against the National Do Not Call Registry before calling/texting.
    text = text or ""
    seen: set = set()
    phones = [ph for line in text.splitlines() for ph in _phones_in_line(line, seen)]
    phones.sort(key=lambda ph: 0 if ph["type"] == "wireless" else 1)  # wireless first, stable otherwise
    return {
        "primary_phone": phones[0]["number"] if phones else None,
        "phones": phones,
        "secondary_phones": [ph["number"] for ph in phones[1:]],
        "emails": list(dict.fromkeys(e.lower() for e in _EMAIL_RE.findall(text))),
        "related_people": _related_people(text),
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
    """Return True for a fixed FHA/VA/USDA loan at <= 3.0%, None when type or rate is unknown."""
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
    """Return True when the owner mails elsewhere, None when the mailing address is unknown."""
    if not mailing:
        return None
    return normalize_street(property_address) != normalize_street(mailing)


def add_business_days(start: date, days: int) -> date:
    """Return the date `days` weekdays after `start` (holidays are not skipped)."""
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


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
        if not 200 <= resp.status_code < 300:
            raise IntakeError(f"Notion {method} {path} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json()
    raise IntakeError("unreachable")


def _title_of(page: dict) -> str:
    prop = page.get("properties", {}).get("Address", {})
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))


def find_existing(address: str) -> Optional[str]:
    """Return the page id whose normalized street matches exactly, among rows with the house number."""
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


def _page_notes(page_id: str) -> str:
    page = _request("GET", f"/pages/{page_id}")
    return "".join(t.get("plain_text", "")
                   for t in page.get("properties", {}).get("Notes", {}).get("rich_text", []))


def _update_existing(page_id: str, deal: Dict[str, Any], props: Dict[str, Any], blocks: List[dict]) -> str:
    """Patch an existing row without clobbering it; append the dossier unless this run is already there."""
    # Defaults fill new rows only; on an existing row they would overwrite real pipeline data.
    for name in deal.get("defaulted", []):
        props.pop(name, None)
    old_notes = _page_notes(page_id)
    marker = f"run {deal['run_hash']}"
    if "Notes" in props and marker in old_notes:
        props.pop("Notes")
    elif "Notes" in props and old_notes:
        props["Notes"] = {"rich_text": _rt(deal["notes_property"] + "\n" + old_notes)}
    _request("PATCH", f"/pages/{page_id}", {"properties": props})
    if marker in _existing_text(page_id):
        return "unchanged (already appended)"
    _append(page_id, blocks)
    return "appended"


def upsert_deal(deal: Dict[str, Any], contacts: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Create the row, or update the matching one and append the dossier once per run hash."""
    props = build_properties(deal)
    missing = missing_fields(deal)
    blocks = build_dossier(deal, contacts, missing)
    if dry_run:
        return {"action": "dry-run", "properties": props, "blocks": len(blocks), "missing": missing}

    page_id = find_existing(deal["address"])
    if page_id:
        dossier = _update_existing(page_id, deal, props, blocks)
        return {"action": "updated", "page_id": page_id, "dossier": dossier, "missing": missing}

    page = _request("POST", "/pages", {
        "parent": {"type": "data_source_id", "data_source_id": DATA_SOURCE_ID},
        "properties": props, "children": blocks[:100]})
    if len(blocks) > 100:
        _append(page["id"], blocks[100:])
    return {"action": "created", "page_id": page["id"], "url": page.get("url"), "missing": missing}


# ------------------------------------------------------------------ CLI

NEXT_ACTION_DEFAULT = "Fill missing tax-record and contact fields; verify loan with seller/servicer."
NEXT_ACTION_FORECLOSURE = "ATTORNEY FIRST: verify docket and get NJ attorney review before any owner contact."


@dataclass
class _Picker:
    """Look a field up in the flags, then --facts, then the default (recorded in `defaulted`)."""

    # An update skips properties that only hold defaults, so it never overwrites real pipeline data.

    args: argparse.Namespace
    facts: Dict[str, Any]
    defaulted: List[str] = field(default_factory=list)

    def __call__(self, name: str, default: Any = None, prop: Optional[str] = None) -> Any:
        """Return the first non-empty value for `name`."""
        v = getattr(self.args, name, None)
        if v not in (None, ""):
            return v
        if self.facts.get(name) not in (None, ""):
            return self.facts[name]
        if prop and default is not None:
            self.defaulted.append(prop)
        return default


def _load_facts(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _record_fields(pick: _Picker, address: str) -> Dict[str, Any]:
    """Stage 1: tax and parcel record."""
    mailing = pick("mailing_address")
    return {
        "town": pick("town"), "county": pick("county"), "apn": pick("apn"),
        "block_lot": pick("block_lot"), "owner_name": pick("owner_name"),
        "property_class": pick("property_class"), "assessed_value": _num(pick("assessed_value")),
        "annual_taxes": _num(pick("annual_taxes")), "last_sale": pick("last_sale"),
        "mailing_address": mailing, "absentee": is_absentee(address, mailing),
        "out_of_state": bool(mailing and not re.search(r"\b(NJ|New Jersey)\b", mailing, re.I)),
    }


def _loan_summary(loan_type: Optional[str], rate: Optional[float], fixed: bool,
                  balance: Optional[float], source: Optional[str]) -> Optional[str]:
    terms = ("fixed" if fixed else "ARM") if loan_type else None
    bits = [loan_type, None if rate is None else f"{rate}%", terms,
            None if balance is None else f"balance ${balance:,.0f}", source]
    return ", ".join(b for b in bits if b) or None


def _value_fields(pick: _Picker) -> Dict[str, Any]:
    """Stage 3: value, loan, assumable check, and equity-gap plan."""
    rate, loan_type = _num(pick("rate")), pick("loan_type")
    fixed = str(pick("loan_fixed", "yes")).lower() not in ("no", "false", "arm", "0")
    asking, balance = _num(pick("asking")), _num(pick("loan_balance"))
    plan = equity_gap_plan(asking, balance)
    return {
        "specs": pick("specs"), "asking_price": asking, "est_value": pick("est_value"),
        "arv": _num(pick("arv")), "comps": pick("comps"),
        "loan_summary": _loan_summary(loan_type, rate, fixed, balance, pick("loan_source")),
        "assumable_ok": assess_assumable(loan_type, rate, fixed),
        "gap_plan": plan, "equity_gap": plan["equity_gap"] if plan else None,
    }


def _pipeline_fields(pick: _Picker, foreclosure: bool, today: date) -> Dict[str, Any]:
    """Pipeline selects and the next action; a foreclosure always reaches the card."""
    fields = {
        "strategy": pick("strategy"), "stage": pick("stage", "2 Researching", "Stage"),
        "call": pick("call", "Undecided", "Call"), "confidence": pick("confidence", "Low", "Confidence"),
        "lead_source": pick("lead_source", "Auction / sheriff" if foreclosure else None, "Lead source"),
        # True or None: an intake never clears an Attorney first flag someone already set.
        "attorney_first": True if foreclosure else None,
    }
    days = int(pick("followup_days", 2, "Next action date"))
    fields["next_action_date"] = add_business_days(today, days).isoformat()
    fields["next_action"] = pick("next_action", NEXT_ACTION_FORECLOSURE if foreclosure else NEXT_ACTION_DEFAULT,
                                 "Next action")
    if foreclosure:
        pick.defaulted[:] = [n for n in pick.defaulted if n not in ("Next action", "Next action date")]
    return fields


def _resolve_strategy(deal: Dict[str, Any], defaulted: List[str]) -> None:
    if deal["strategy"] is None:
        deal["strategy"] = "Assumable" if deal["assumable_ok"] else "Subject-To"
        if not deal["assumable_ok"]:
            defaulted.append("Strategy")


def _validate(deal: Dict[str, Any]) -> None:
    for key, allowed in [("strategy", STRATEGIES), ("stage", STAGES), ("call", CALLS),
                         ("confidence", CONFIDENCE)]:
        if deal[key] not in allowed:
            raise IntakeError(f"{key} must be one of {allowed}")
    if deal["lead_source"] and deal["lead_source"] not in LEAD_SOURCES:
        raise IntakeError(f"lead_source must be one of {LEAD_SOURCES}")


def _summary_notes(deal: Dict[str, Any], contacts: Dict[str, Any]) -> str:
    operator = deal["operator"]
    summary = [f"[intake {deal['run_date']}{' by ' + operator if operator else ''}, run {{hash}}]"]
    if deal["assumable_ok"]:
        summary.append("Assumable <=3% - live-in candidate (unverified until servicer confirms).")
    if contacts["primary_phone"]:
        summary.append("Contacts from pasted skip-trace: UNVERIFIED, DNC not checked.")
    if deal["notes"]:
        summary.append(deal["notes"])
    return " ".join(summary)


def _run_hash(deal: Dict[str, Any], contacts: Dict[str, Any]) -> str:
    """Hash the facts only, so a rerun with the same facts on any day never appends a second dossier."""
    volatile = ("run_date", "next_action_date", "notes_property")
    fingerprint = json.dumps({k: v for k, v in deal.items() if k not in volatile},
                             sort_keys=True, default=str) + json.dumps(contacts, sort_keys=True)
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:10]


def build_deal(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Merge flags over --facts, derive the computed fields, and validate select values."""
    facts = _load_facts(args.facts)
    pick = _Picker(args, facts)
    contacts = parse_skip_trace(pick("skip_text", "") or "")
    foreclosure = bool(args.foreclosure or facts.get("foreclosure"))
    today = date.today()
    deal: Dict[str, Any] = {"address": args.address.strip()}
    deal.update(_record_fields(pick, args.address))
    deal.update(_value_fields(pick))
    deal.update({"owner_phone": contacts["primary_phone"], "owner_email": (contacts["emails"] or [None])[0],
                 "operator": pick("operator") or os.getenv("DEAL_DESK_OPERATOR"), "notes": pick("notes"),
                 "run_date": today.isoformat()})
    deal.update(_pipeline_fields(pick, foreclosure, today))
    _resolve_strategy(deal, pick.defaulted)
    _validate(deal)
    deal["notes_property"] = _summary_notes(deal, contacts)
    if contacts["related_people"]:
        deal["contact_person"] = "Listed related people (unverified): " + ", ".join(contacts["related_people"])
    deal["run_hash"] = _run_hash(deal, contacts)
    deal["notes_property"] = deal["notes_property"].replace("{hash}", deal["run_hash"])
    deal["defaulted"] = pick.defaulted
    return deal, contacts


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the CLI; --skip-file contents are appended to --skip-text."""
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
    """Run the CLI: print the JSON result, return 0 on success and 2 on IntakeError."""
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
