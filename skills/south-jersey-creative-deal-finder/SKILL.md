---
name: south-jersey-creative-deal-finder
description: Run the South Jersey Deal Desk 4-stage property intake (tax/parcel record, skip-trace contacts, valuation with assumable-loan and equity-gap sizing, Notion Deals Pipeline upsert with dossier and owner pitch script) for one address. Use when the user or a teammate such as Fred says "add <address> to the deal desk", "run intake on <address>", "is this assumable", "skip trace this owner", or shares a South Jersey property screenshot or address for creative financing (Subject-To, Assumable, Lease Option, Flip, Rental). Dedupes against existing rows and is safe to rerun.
---

# South Jersey creative deal finder

`scripts/deal_desk_intake.py` (with the page builders in `scripts/deal_desk_dossier.py`; keep both files together) writes one property into the Notion **Deals Pipeline** (Real Estate Deal Desk). It is idempotent. A row with the same normalized street address is updated, never duplicated. A dossier with identical facts is never appended twice. Defaults (stage, call, confidence, next action) only fill new rows, so a rerun never overwrites pipeline data a person already set.

The script does no scraping. Stages 1-3 are researched by the operator or the agent and passed in as flags or a `--facts` JSON file. Anything not supplied is listed under "Still missing", never guessed.

## Fred: how to run it

One-time setup on Fred's machine (or any operator's):

```bash
pip install requests                                  # Python 3.9+
export NOTION_TOKEN="<Notion internal integration secret>"   # never commit this
export DEAL_DESK_OPERATOR="Fred"                      # stamped on every card and dossier
# In Notion: Deals Pipeline > ... > Connections > add the integration that owns the token.
# Optional, to call it from anywhere:
ln -s "$PWD/bin/deal-desk-intake" ~/.local/bin/deal-desk-intake
```

Every run: **dry run first**, read the payload, then write.

```bash
S=scripts/deal_desk_intake.py   # or the deal-desk-intake launcher

python3 $S --address "100 Example Rd, Sewell, NJ 08080" --town "Washington Twp, Gloucester County" --dry-run
python3 $S --address "100 Example Rd, Sewell, NJ 08080" --town "Washington Twp, Gloucester County"

# Foreclosure / lis pendens / sheriff sale: sets Attorney first, holds the pitch
python3 $S --address "12 Example Ave, Glassboro, NJ" --foreclosure --strategy Subject-To

# Contacts: paste skip-trace text, or point at a file
python3 $S --address "..." --skip-text "John Doe (856) 555-0101 Wireless Verizon last seen 2026 ..."
python3 $S --address "..." --skip-file owner_trace.txt

# Everything at once from a facts file (flags win over the file)
python3 $S --address "..." --facts facts.json --operator Fred
```

Exit code 0 prints a JSON result (`created`, `updated`, or `dry-run` plus `missing`). Exit code 2 means bad input or a Notion error, and nothing partial is hidden. Another Claude session acting for Fred runs the same commands with `--operator Fred`.

## The 4 stages

**1. Tax & parcel record.** Get these from njparcels.com or the county/municipal tax assessor: county, municipality, APN, block/lot, owner of record, property class, assessed value, net annual taxes, last sale, and owner mailing address. The flags are `--county --apn --block-lot --owner-name --property-class --assessed-value --annual-taxes --last-sale --mailing-address`. Absentee and out-of-state status is computed from the mailing address.

**2. Contacts (skip trace).** The operator runs the people-search or skip-trace and pastes the text into `--skip-text` or `--skip-file`. The parser extracts phones with type, carrier and recency (wireless first), emails, and co-owners, spouses, heirs and relatives. Everything stays **UNVERIFIED, DNC not checked** until a person confirms the match and scrubs against the National Do Not Call Registry. Use contacts only to discuss buying the property. Never use them for credit, employment or tenant screening (FCRA).

**3. Value & debt sizing.** The flags are `--specs --asking --est-value --arv --comps --loan-type --rate --loan-fixed --loan-balance --loan-source`.

- A loan qualifies as an **assumable live-in candidate** when it is an FHA, VA or USDA loan, fixed rate, at ≤3.0%. If the operator didn't pick a strategy, Strategy becomes Assumable.
- **Equity gap** = asking minus loan balance. The proposal is up to $10,000 cash plus the remainder as a seller-carryback 2nd note. That is a proposal only: it needs servicer approval and an attorney-drafted note.
- ARV comes from renovated sold comps only. A listing estimate is not ARV.

**4. Notion intake.** The data source defaults to `8303402e-fa35-4d41-84b5-dc48fa52376d` (override with `DEALS_DATA_SOURCE_ID`).

- **Fields.** It fills Address, Town, Owner name, Owner mailing address, Owner phone, Owner email, Contact person, Block / lot, Property class, Assessed value, Annual taxes, Last sale, Beds / baths / sq ft, Loan type / rate, Asking price, ARV, Equity gap, Absentee owner, Strategy, Stage, Call, Confidence, Lead source, Attorney first, Next action, Next action date and Notes.
- **Dossier.** It appends a dossier with the stage 1-3 findings, a "Still missing" list with where to get each item, and the owner pitch script.

## Compliance rules (non-negotiable)

- **Foreclosure = attorney first.** `--foreclosure` adds a red warning: NJ's Foreclosure Rescue Fraud Prevention Act (N.J.S.A. 46:10B-53 et seq.) may apply. It also sets Attorney first, replaces the pitch with "on hold", and sets the next action to attorney review. No owner contact, terms, or contracts go out before a NJ attorney reviews the deal. This is a risk flag, not legal advice.
- Rerunning never clears an Attorney first flag.
- Outreach is drafted, never auto-sent. Mail first. Call or text only DNC-scrubbed numbers, with no autodialed or prerecorded calls or texts to cell phones without consent (TCPA).
- Label facts verified / estimate / unknown. Never invent owner, loan or valuation data.
- Never store SSNs, bank or card numbers, passwords or government ID numbers in Notion or in the facts file.
- Respect site terms and access controls. Don't evade CAPTCHAs, and don't present uncertain identity matches as confirmed.

## facts.json keys

Any flag name with underscores works as a key, e.g.:

```json
{
  "town": "Glassboro, Gloucester County",
  "owner_name": "...",
  "block_lot": "Block 1 Lot 2",
  "annual_taxes": 6123,
  "mailing_address": "...",
  "specs": "3 bd / 1 ba / 1,100 sf",
  "asking": 240000,
  "loan_type": "FHA",
  "rate": 2.75,
  "loan_fixed": "yes",
  "loan_balance": 205000,
  "foreclosure": false,
  "notes": "Vacant, overgrown yard."
}
```
