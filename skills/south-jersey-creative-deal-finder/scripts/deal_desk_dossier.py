"""Notion page payloads for the Deal Desk intake: card properties, intake dossier, missing-facts list."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RICH_TEXT_LIMIT = 2000

ATTORNEY_WARNING = (
    "ATTORNEY FIRST - legal-risk flag, not legal advice. This property is listed as "
    "pre-foreclosure, lis pendens, or sheriff sale. NJ's Foreclosure Rescue Fraud Prevention Act "
    "(N.J.S.A. 46:10B-53 et seq.) may apply, including equity-purchaser and foreclosure-consultant "
    "rules. Do not contact the homeowner, pitch terms, or deliver contracts until a qualified New "
    "Jersey real estate attorney has reviewed title, the docket, and the transaction structure."
)


def rich_text(text: str) -> List[dict]:
    """Split text into Notion rich_text objects of at most 2000 characters."""
    text = text or ""
    return [{"type": "text", "text": {"content": text[i:i + RICH_TEXT_LIMIT]}}
            for i in range(0, len(text), RICH_TEXT_LIMIT)] or [{"type": "text", "text": {"content": ""}}]


def _para(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(text)}}


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text(text)}}


# (Notion property, deal key, Notion type), in the order the card shows them
_PROPERTY_MAP = [
    ("Town", "town", "rich_text"), ("Owner name", "owner_name", "rich_text"),
    ("Owner mailing address", "mailing_address", "rich_text"), ("Block / lot", "block_lot", "rich_text"),
    ("Property class", "property_class", "rich_text"), ("Last sale", "last_sale", "rich_text"),
    ("Beds / baths / sq ft", "specs", "rich_text"), ("Loan type / rate", "loan_summary", "rich_text"),
    ("Contact person", "contact_person", "rich_text"), ("Next action", "next_action", "rich_text"),
    ("Notes", "notes_property", "rich_text"),
    ("Assessed value", "assessed_value", "number"), ("Annual taxes", "annual_taxes", "number"),
    ("ARV", "arv", "number"), ("Asking price", "asking_price", "number"), ("Equity gap", "equity_gap", "number"),
    ("Strategy", "strategy", "select"), ("Stage", "stage", "select"), ("Call", "call", "select"),
    ("Confidence", "confidence", "select"), ("Lead source", "lead_source", "select"),
    ("Attorney first", "attorney_first", "checkbox"), ("Absentee owner", "absentee", "checkbox"),
    ("Owner phone", "owner_phone", "phone_number"), ("Owner email", "owner_email", "email"),
    ("Next action date", "next_action_date", "date"),
]
_KEEP_FALSY = {"number", "checkbox"}  # 0 and False are real values for these types


def _prop_value(kind: str, value: Any) -> Dict[str, Any]:
    if kind == "rich_text":
        return {"rich_text": rich_text(str(value))}
    if kind == "select":
        return {"select": {"name": value}}
    if kind == "date":
        return {"date": {"start": value}}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    return {kind: value}  # number, phone_number, email


def build_properties(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Map deal fields to Deals Pipeline properties, omitting missing values so updates never blank data."""
    p: Dict[str, Any] = {"Address": {"title": rich_text(deal["address"])}}
    for name, key, kind in _PROPERTY_MAP:
        value = deal.get(key)
        if value is not None and (value or kind in _KEEP_FALSY):
            p[name] = _prop_value(kind, value)
    return p


_STAGE1_FIELDS = [("County", "county"), ("Municipality", "town"), ("APN / parcel ID", "apn"),
                  ("Block / lot", "block_lot"), ("Owner of record", "owner_name"),
                  ("Property class", "property_class"), ("Assessed value", "assessed_value"),
                  ("Net annual taxes", "annual_taxes"), ("Last sale", "last_sale"),
                  ("Owner mailing address", "mailing_address")]
_STAGE3_FIELDS = [("Specs", "specs"), ("Asking price", "asking_price"), ("Estimated value", "est_value"),
                  ("ARV (from renovated sold comps)", "arv"), ("Comps", "comps"), ("Loan", "loan_summary")]
_ASSUMABLE_TEXT = {True: "YES - live-in candidate (servicer must confirm)", False: "no"}
_PITCH = ("Hi {owner}, my name is [Your name]. I'm a local buyer and I'm interested in {address}. "
          "If you've thought about selling, I can buy it as-is and work around your timeline. I'd put a "
          "written offer in front of you that you can review with an attorney or anyone you trust. No "
          "obligation. If you'd rather not hear from me, just tell me and I won't contact you again. "
          "[Your phone / email]")


def _fact_bullets(deal: Dict[str, Any], fields: List[Tuple[str, str]]) -> List[dict]:
    return [_bullet(f"{label}: {'Not found' if deal.get(key) in (None, '') else deal[key]}")
            for label, key in fields]


def _stage1_blocks(deal: Dict[str, Any]) -> List[dict]:
    blocks = [_heading("Stage 1 - Tax & parcel record")] + _fact_bullets(deal, _STAGE1_FIELDS)
    if deal.get("absentee") is not None:
        where = " (out of state)" if deal.get("out_of_state") else ""
        blocks.append(_bullet(f"Absentee owner: {'yes' if deal['absentee'] else 'no'}{where}"))
    return blocks


def _phone_line(ph: Dict[str, Optional[str]]) -> str:
    recency = f"last seen {ph['recency']}" if ph["recency"] else None
    extra = ", ".join(x for x in [ph["type"], ph["carrier"], recency] if x)
    return f"{ph['number']} - {extra}" if extra else str(ph["number"])


def _stage2_blocks(contacts: Dict[str, Any]) -> List[dict]:
    phones = [_bullet(_phone_line(ph)) for ph in contacts["phones"]] or [
        _bullet("Phones: Not found - paste skip-trace / people-search text with --skip-text")]
    return [_heading("Stage 2 - Contacts (UNVERIFIED, DNC not checked)")] + phones + [
        _bullet(f"Emails: {', '.join(contacts['emails']) or 'Not found'}"),
        _bullet(f"Co-owners / spouses / heirs / relatives listed: "
                f"{', '.join(contacts['related_people']) or 'none listed'}"),
        _para("Confirm each person matches the owner and the property or mailing address, "
              "and scrub phones against the National Do Not Call Registry before any call or text.")]


def _gap_text(plan: Optional[Dict[str, float]]) -> str:
    if not plan:
        return "Equity gap: unknown - need price and loan balance"
    return (f"Equity gap ${plan['equity_gap']:,.0f}: proposed ${plan['cash']:,.0f} cash + "
            f"${plan['seller_carryback_2nd']:,.0f} seller carryback 2nd note "
            f"(0%-low interest). Proposal only: servicer approval + attorney-drafted note.")


def _stage3_blocks(deal: Dict[str, Any]) -> List[dict]:
    assumable = _ASSUMABLE_TEXT.get(deal.get("assumable_ok"), "unknown - need loan type, rate, fixed/ARM")
    return [_heading("Stage 3 - Value & debt sizing")] + _fact_bullets(deal, _STAGE3_FIELDS) + [
        _bullet("Assumable <=3.0% fixed FHA/VA/USDA: " + assumable),
        _bullet(_gap_text(deal.get("gap_plan")))]


def _pitch_blocks(deal: Dict[str, Any]) -> List[dict]:
    heading = _heading("Owner pitch script")
    if deal.get("attorney_first"):
        return [heading, _para("Pitch on hold - attorney review required before any owner contact.")]
    owner = deal.get("owner_name") or "[Owner name]"
    return [heading, _para(_PITCH.format(owner=owner, address=deal["address"])),
            _para("Mail first. Call or text only numbers that passed a DNC scrub.")]


def build_dossier(deal: Dict[str, Any], contacts: Dict[str, Any], missing: List[str]) -> List[dict]:
    """Build the page blocks for one intake run: warning, stages 1-3, missing list, pitch."""
    blocks: List[dict] = []
    if deal.get("attorney_first"):
        blocks.append({"object": "block", "type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "⚠️"}, "color": "red_background",
            "rich_text": rich_text(ATTORNEY_WARNING)}})
    blocks.append(_heading(f"Intake dossier {deal['run_date']} (run {deal['run_hash']})"))
    blocks.append(_para(f"Operator: {deal.get('operator') or 'not set'}. Strategy {deal.get('strategy')}, "
                        f"stage {deal.get('stage')}, call {deal.get('call')}, confidence {deal.get('confidence')}."))
    blocks += _stage1_blocks(deal) + _stage2_blocks(contacts) + _stage3_blocks(deal)
    blocks += [_heading("Still missing")] + [_bullet(m) for m in missing or ["Nothing flagged"]]
    if deal.get("notes"):
        blocks += [_heading("Notes"), _para(deal["notes"])]
    return blocks + _pitch_blocks(deal)


def missing_fields(deal: Dict[str, Any]) -> List[str]:
    """List what the operator still has to research, with where to get each item."""
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
