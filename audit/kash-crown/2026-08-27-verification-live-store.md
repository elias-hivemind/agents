# KASH CROWN — Independent Verification Against Live Store

**Date:** 2026-08-27
**Method:** Shopify Admin GraphQL, read-only. **No mutations were issued.**
**Store confirmed:** KASH CROWN / `www.kashcrown.com` / Basic / USD / CDT / United States

This pass re-checked Batch 1 against the live store rather than trusting the narrative.
Three findings held. **Two were wrong. Two blocked items are now closed.**

---

## Summary

| Batch 1 finding                              | Verdict                                                             |
| -------------------------------------------- | ------------------------------------------------------------------- |
| 5 products contained as DRAFT                | **Confirmed** — all five still DRAFT                                |
| Built Under Pressure hoodie live and branded | **Confirmed** — ACTIVE, vendor KASH CROWN, type HOODIE              |
| Second sales channel unauthorised            | **Confirmed still live** — Microsoft Copilot published              |
| C1 compare-at "cannot verify"                | **Closed — worse.** Store has zero orders. All 19 are unsupportable |
| C4 "$75 threshold unverified"                | **Closed — finding was wrong.** No threshold exists anywhere        |
| H1 "five delivery profiles"                  | **Wrong — there are nine**                                          |

---

## 1. Containment holds

All five products queried by explicit ID. Every one returns `status: DRAFT`, `vendor: Apliiq`.
Nothing has been reverted or re-published. See `evidence/product-status.md`.

## 2. The new hoodie is correct — except the channel

`gid://shopify/Product/10837409661240` —
`KASH CROWN Built Under Pressure Hoodie — Pigment Black`

- `status: ACTIVE`, `vendor: KASH CROWN`, `productType: HOODIE` — all correct
- `resourcePublications` returns **two** published channels:
  - `Online Store` → `isPublished: true` ✅ intended
  - `Microsoft Copilot` → `isPublished: true` ❌ **not authorised, still live**

This item from the handover list has **not** been resolved. It still needs the manual unpublish
(product → Publishing → uncheck Microsoft Copilot), or explicit approval to do it via API.

## 3. C1 — compare-at pricing: closed, and it is worse than "unverifiable"

```
ordersCount(limit: 1000) → { count: 0, precision: "EXACT" }
```

**The store has never taken an order.**

Batch 1 said this "needs order history" to resolve. The order history exists and it is empty.
A compare-at price represents a price at which the item was actually offered and sold. With zero
orders, not one of the 19 compare-at prices can be substantiated by anything. This is no longer
"cannot verify" — it is settled. Every "Sale" badge on the storefront references a price the
item has never sold at.

Confirmed the count exactly: **19 of 21 active products carry a compare-at price.** The only two
without are Built Under Pressure ($98) and Gilt Heavyweight ($58) — the two newest products.
Full table in `evidence/pricing-compare-at.md`.

This is the single largest exposure in the audit and it is live right now.

## 4. C4 — the free-shipping claim: closed, and Batch 1 had it backwards

Batch 1 could not read the rates and recorded the $75 threshold as "unverified." The corrected
query runs fine. The result:

**There is no $75 threshold anywhere in the shipping configuration.** Not misconfigured — absent.
Across all nine profiles, **zero** method conditions reference order subtotal. The only conditions
that exist in the entire store are `TOTAL_WEIGHT` bands on the Apliiq profile.

What actually happens on a US order:

| Profile                                     | US rate                   | Free?          |
| ------------------------------------------- | ------------------------- | -------------- |
| General profile (default)                   | $0.00, no conditions      | Always free    |
| Printful: Tshirts (#PF-FRG1)                | $0.00                     | Always free    |
| Printful: Tshirts (#PF-FRG1001)             | $0.00                     | Always free    |
| Zendrop                                     | $0.00                     | Always free    |
| Printful: Stickers (#PF-FRG10)              | $4.29                     | **Never free** |
| Printful: Snapbacks & Buckets (#PF-FRG1096) | $4.49                     | **Never free** |
| Printful: Pin buttons (#PF-FRG35)           | $4.50                     | **Never free** |
| Printful: Hoodies (#PF-FRG2)                | $8.49                     | **Never free** |
| Apliiq Print On Demand                      | $5.96 → $157.99 by weight | **Never free** |

So the marquee is wrong in both directions simultaneously:

- Tees ship free at **any** cart value, including a $20 order — the $75 gate is fiction, and it
  under-sells what the store actually offers.
- Hoodies, hats, pins, stickers and **every Apliiq item** are **never** free — not at $75,
  not at $500.

**The sharpest case:** the $98 Built Under Pressure hoodie sits on the Apliiq profile. The newest
flagship product advertises "FREE SHIPPING ON ORDERS $75+" sixteen times above the fold, then adds
$5.96 or more at checkout. That is a bait-and-switch pattern on the highest-value item in the
catalog, and it is live.

## 5. H1 — nine delivery profiles, not five

Batch 1 listed five. There are nine. Missed:

- `Printful: Pin buttons (#PF-FRG35)` — `134587842872`
- `Printful: Stickers (#PF-FRG10)` — `134588072248`
- `Zendrop` — `134653772088`
- `Apliiq Print On Demand` — `135041614136`

Split-shipment and multiple-charge exposure is correspondingly wider than reported. Full detail in
`evidence/delivery-profiles.md`.

---

## Not re-verified in this pass

C2 (Made in USA), C3 (ships 2–5 days), H2, H3, H4, M1, M2 were not re-checked — they depend on
storefront copy and policy page bodies, which are Batch 2 scope. They stand as written in Batch 1.

Printful garment specs remain **blocked** — no Printful connector is reachable in this session.
The seven affected products still cannot have blank, weight, fit, composition or cost verified.
