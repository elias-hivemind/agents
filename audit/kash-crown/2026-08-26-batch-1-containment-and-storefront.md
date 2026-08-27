# KASH CROWN — Full Store Audit: Batch 1

**Batch scope:** Emergency containment + live storefront scan

**Date:** 2026-08-26
**Scope:** read-only except where marked EXECUTED

> Preserved as originally written. See `2026-08-27-verification-live-store.md` for
> corrections — two findings in section 2 were wrong, and two blocked items are now closed.

---

## 0. Root cause

Apliiq's "add to store" button publishes to Shopify as ACTIVE, immediately, with raw supplier
copy, vendor `Apliiq`, a supplier-worded title, zero tags, and no SEO fields.

Every raw listing found in this audit entered the store the same way. This is not five separate
mistakes — it is one mechanism firing five times. Until the workflow changes, every future
"add to store" click puts an unbranded supplier page live on kashcrown.com within seconds.

Evidence: all five raw products carry `vendor: "Apliiq"`, `tags: []`, `seo: {title: null,
description: null}`, and Apliiq B2B marketing copy addressed to _"brand owners"_ with outbound
links to `apliiq.com`.

Three of them went live during the audit (00:00:57, 00:06:43, 00:08:03) — while scanning.

---

## 1. Emergency containment — EXECUTED

Status change only. No deletion. No change to artwork, media, variants, supplier, price,
description, title, or handle.

| #   | Title (before)                            | Handle                                      | Product ID       | Before | After | Reason                                                                                      |
| --- | ----------------------------------------- | ------------------------------------------- | ---------------- | ------ | ----- | ------------------------------------------------------------------------------------------- |
| 1   | Gildan Heavyweight Tee Rapid Fulfillment  | `gildan-heavyweight-tee-rapid-fulfillment`  | `10837647491384` | ACTIVE | DRAFT | Supplier title, vendor `Apliiq`, Gildan 5000 boilerplate, apliiq.com links, no tags, no SEO |
| 2   | Gildan Heavy Blend Hoodie Speedy Delivery | `gildan-heavy-blend-hoodie-speedy-delivery` | `10837545615672` | ACTIVE | DRAFT | Same failure class. Also "Speedy Delivery" in a customer-facing title                       |
| 3   | Tultex Womens Poly Rich T Shirt           | `tultex-womens-poly-rich-t-shirt`           | `10837860876600` | ACTIVE | DRAFT | Went live 00:00:57. B2B copy: "built for brand owners", apliiq.com link                     |
| 4   | Women's Premium Cotton T Shirt            | `womens-premium-cotton-t-shirt`             | `10837862318392` | ACTIVE | DRAFT | Went live 00:06:43. Same B2B copy                                                           |
| 5   | Women's Premium Cotton T Shirt            | `womens-premium-cotton-t-shirt-1`           | `10837862416696` | ACTIVE | DRAFT | Went live 00:08:03. Byte-identical duplicate of #4, 80 seconds apart                        |

Verification source: Shopify Admin GraphQL `productUpdate` response, each returning the exact
product ID, handle, title, and `status: "DRAFT"` with `userErrors: []`.

Items 3–5 were not on the owner's named list; they were drafted under the standing rule
permitting immediate draft of materially misleading live copy. **Flagged as OWNER DECISION.**

Note on a reversal: an earlier owner instruction was "Leave them live, brand them instead" for
items 1–2. A newer instruction said draft immediately. The newer, more specific one was followed.

### Crown & Dice Hoodie — confirmed clean

`kash-crown-crown-dice-hoodie-black` / `10821951979832`

- productType: HOODIE (corrected earlier in session from SWEATPANTS)
- Collections: `kash-crown`, `hoodies`, `new-arrivals`, `wear-the-crown-shop-all`, `apparel`
- Not in any sweatpants collection. No filtering or navigation break.

---

## 2. Live storefront scan — homepage

Nav: HOME · SHOP · SIZE CHART · ABOUT · FAQ · CONTACT.

### CRITICAL

**C1 — Every branded product is permanently "on sale."**
19 of 21 branded products carry a compare-at price. Homepage shows a "Sale" badge on all four
featured items. Full table in `evidence/pricing-compare-at.md`.

A compare-at price represents a price the item was actually offered at. Could not verify from
product records whether these were ever the real selling price — needs order history.
**BLOCKED / NEEDS OWNER DECISION.** → _Closed in verification pass._

**C2 — "MADE TO ORDER IN THE US" (hero) and "MADE TO ORDER - PRINTED IN THE US" (banner).**
The Gilt tee's blank is made in Honduras (Apliiq product record, `shmhss`). Printing in the US
and _making_ in the US are different claims; the FTC treats "Made in USA" as a strict standard.
"Printed in the US" is likely defensible; "made to order in the US" reads as origin.
Printful fulfilment locations unverified. **BLOCKED / NEEDS OWNER DECISION.**

**C3 — "Ships 2-5 days" contradicts supplier copy.**
Homepage: "Ships 2-5 days." Apliiq's own product text, live on the Gilt listing:
"typically produced in about a week." Both cannot be true. **BLOCKED / NEEDS OWNER DECISION.**

**C4 — "FREE SHIPPING ON ORDERS $75+" repeated 16 times in a scrolling marquee.**
Delivery profiles pulled. General profile has one Domestic method "Standard" and one
International method. Could not read rate amounts or any $75 threshold — first query failed with
a GraphQL syntax error and the corrected query returned method names without prices.
**BLOCKED — needs verification in Shopify Settings → Shipping.** → _Closed in verification pass; finding was wrong._

### HIGH

**H1 — Split-shipment risk confirmed.** Five delivery profiles: General profile (default, Apliiq)
plus four Printful flat-rate profiles — Tshirts (#PF-FRG1), Tshirts (#PF-FRG1001),
Snapbacks and Bucket hats (#PF-FRG1096), Hoodies (#PF-FRG2). A cart mixing an Apliiq tee and a
Printful tee draws rates from separate profiles → multiple shipping charges and two parcels.
Two separate Printful T-shirt profiles is itself suspicious. → _Undercounted; see verification._

**H2 — "HEAVYWEIGHT STREETWEAR" as storewide positioning.** Catalog includes a 3.6 oz poly-blend
women's tee, three tees tagged `lightweight` at $19.99, and a 5.3 oz Gildan 5000. The hero claim
doesn't describe the catalog.

**H3 — "Premium streetwear crafted for those who wear the crown"** repeated 16 times in the same
marquee. "Premium" is on the banned-unless-verified list.

**H4 — Newsletter promises "Member pricing."** No membership or pricing mechanism verified.

### MEDIUM

**M1 — The $58 Gilt flagship is absent from Featured Drops.** The four featured items are $32–$68
and all badged "Sale." The newest and most expensive piece isn't shown.

**M2 — Policy pages exist** (Contact, Privacy, Refund, Shipping, Terms) but resolve to
`checkout.shopify.com/100399907128/policies/...` URLs. Content not read — queued for Batch 2.

---

## 3. Errors encountered (reported, not worked around)

1. `deliveryProfiles` query with `rateProvider`/`methodConditions` →
   `syntax error, unexpected end of file at [1, 474]`. Query was malformed. Re-ran a simpler
   read-only version. Rate amounts and the $75 threshold remained unread.
   → _Resolved in verification pass._
2. `shop { privacyPolicy refundPolicy termsOfService shippingPolicy }` →
   `Field 'privacyPolicy' doesn't exist on type 'Shop'`. Correct field is `shop.shopPolicies`.
   Re-ran successfully.

Both were read-only. No product was touched by either.

---

## 4. Not yet done — queued

- **Batch 2:** collection pages, every live product page, search, cart, recommendations,
  About/FAQ/Contact, all five policy bodies, mobile viewport, non-payment checkout to
  shipping-method step
- **Batch 3:** master catalog audit (one row per product and variant)
- **Batch 4:** pricing and margin report per variant
- **Batch 5:** product-copy and SEO plan
- **Batch 6:** storefront implementation plan

**Printful verification BLOCKED.** No Printful connector reachable from the session. Seven named
products (`crown-heavy-tee`, `wear-the-crown-tee`, `pressure-arch-tee-1`, Royal Crest,
Crowned Dollar, Lion King, Crown & Cash) cannot have blank, weight, fit, composition, or supplier
cost verified until a connector is added or specs are pasted.

Consequence: no tag, copy, or price change touching those seven can proceed, because each turns
on a garment fact with no source. Includes removing "metallic-effect print" from Crowned Dollar —
removable as an unsupported claim, but not confirmable as false without the Printful print config.
