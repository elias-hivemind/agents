# Open Decisions — awaiting owner

Nothing on this list has been actioned. All verification was read-only.

---

## 1. Compare-at pricing on 19 products — LIVE EXPOSURE

**Status:** confirmed, not "unverifiable". Store has zero orders (`ordersCount` = 0, EXACT),
so no compare-at price is supported by any sale. Every "Sale" badge references a price the item
has never sold at.

Options put to the owner:

- **Strip all 19** — remove `compareAtPrice` from every variant. Actual prices unchanged; only the
  fictitious "was" price and Sale badges disappear. Reversible. _(recommended)_
- **Strip worst offenders only** — largest implied discounts first (Pin Set 40%, Patch 30%,
  Essentials tees 29%, Feel The Pressure 28%). Reduces but does not clear exposure.
- **Leave and decide later** — 19 products stay live with fictitious reference prices.

**Owner response: dismissed, no instruction given. No action taken.**

## 2. "$75+ free shipping" banner — LIVE EXPOSURE

**Status:** claim matches nothing in the shipping config. No subtotal condition exists anywhere.
Tees ship free unconditionally; hoodies/hats/pins/stickers/all Apliiq items are never free.
The $98 Built Under Pressure hoodie advertises free shipping and charges $5.96+ at checkout.

Options put to the owner:

- **Fix the banner to match reality** — copy change only, no config change, no margin impact.
- **Build a real $75 threshold** — add subtotal conditions so the promise becomes true. Absorbs
  $4.29–$8.49+ per order on hoodies, hats, accessories and Apliiq items.
- **Pull the banner for now** — stops the misleading claim immediately, no config or margin change.

**Owner response: dismissed, no instruction given. No action taken.**

## 3. Microsoft Copilot sales channel — STILL LIVE

Built Under Pressure Hoodie is published to `Microsoft Copilot` as well as `Online Store`.
Owner previously specified Online Store only. Needs either a manual unpublish in admin
(product → Publishing → uncheck Microsoft Copilot) or explicit approval to do it via API.

## 4. Items 3–5 of the containment — retro-approval

`tultex-womens-poly-rich-t-shirt`, `womens-premium-cotton-t-shirt`, `womens-premium-cotton-t-shirt-1`
were not on the owner's named list. They were drafted under the standing rule on materially
misleading live copy. One-click restore if the owner disagrees.

Also note the reversal: the owner had earlier chosen "leave them live, brand them instead" for
items 1–2; a later instruction said draft immediately. The later instruction was followed.

## 5. Size labels on the new hoodie — deliberately not fixed

Option values are lowercase (`xs, s, m, l, xl, xxl, xxxl`) rather than `XS…3XL`. Apliiq's naming.
Left alone on purpose: option values are a plausible key in Apliiq's variant mapping, and the SKU
mapping had already broken once this session. Cosmetic. Needs a deliberate go-ahead.

## 6. Back print dimensions

Design `6045292` back print is 13" × 15.81"; spec is 12" × 14.6". Does not affect SKUs or
fulfillment. Editable any time; applies to future orders. Mockups would need regenerating.

## 7. Crown & Dice Hoodie — dual-stocked sizes

Sizes S and M are stocked at both Printful and Zendrop, so those two can route to Zendrop.
Live now. Unrelated to the Built Under Pressure launch.

## 8. Printful verification — BLOCKED

No Printful connector reachable in this session. Seven products cannot have blank, weight, fit,
composition or supplier cost verified: `crown-heavy-tee`, `wear-the-crown-tee`,
`pressure-arch-tee-1`, Royal Crest, Crowned Dollar, Lion King, Crown & Cash.

No tag, copy or price change touching those seven can proceed — each turns on a garment fact with
no source. Includes the "metallic-effect print" claim on Crowned Dollar: removable as an
unsupported claim, but not confirmable as false without the print config.

## 9. Cleanup queue

- Shopify draft `10836473577784` — dead, DRAFT, safe to delete
- Apliiq designs `6044836` (two swatches) and other stragglers — only `6045292` is in use
- Open Apliiq ticket about `6043230` is moot; can be withdrawn

---

## Remaining audit batches

| Batch | Scope                                                                                                                                                             | State       |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 2     | Collection pages, all live product pages, search, cart, recommendations, About/FAQ/Contact, five policy bodies, mobile viewport, checkout to shipping-method step | not started |
| 3     | Master catalog audit — one row per product and variant                                                                                                            | not started |
| 4     | Pricing and margin report per variant                                                                                                                             | not started |
| 5     | Product-copy and SEO plan                                                                                                                                         | not started |
| 6     | Storefront implementation plan                                                                                                                                    | not started |
