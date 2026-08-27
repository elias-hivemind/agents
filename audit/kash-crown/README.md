# KASH CROWN — Store Audit Archive

Backup of the kashcrown.com store audit. Store: **KASH CROWN** / `www.kashcrown.com`
(Shopify Basic, USD, CDT, United States).

> **Note on location.** This archive lives in `elias-hivemind/agents`, which is a private
> fork of the Cloudflare Agents TypeScript SDK and is otherwise unrelated to the store.
> It was backed up here because this is the branch the session was assigned.
> The natural home is **`elias-hivemind/kash-crown-ops`** ("KASH CROWN autonomous ops
> system — brand law, agent schema, content pipeline"). Consider moving this directory there.

## Contents

| File | What it is |
|---|---|
| `2026-08-26-batch-1-containment-and-storefront.md` | Batch 1 audit as originally written: root cause, containment actions, storefront scan |
| `2026-08-27-verification-live-store.md` | Independent re-verification against the live store. **Corrects two findings and closes two blocked items.** |
| `evidence/product-status.md` | Live status of the 5 contained products + the new hoodie |
| `evidence/pricing-compare-at.md` | All 21 active products, price vs compare-at |
| `evidence/delivery-profiles.md` | All 9 delivery profiles and their real US rates |
| `open-decisions.md` | What still needs an owner call |

## Reading order

Start with `2026-08-27-verification-live-store.md`. It supersedes parts of Batch 1.

## Status at time of backup

- 5 raw Apliiq products contained as DRAFT — **verified still DRAFT**
- Built Under Pressure Hoodie live and branded — **verified**, but still published to a
  second sales channel that was not authorised
- Compare-at pricing exposure — **confirmed and escalated** (store has zero orders)
- Free-shipping claim — **confirmed false**, and the shipping config is not what Batch 1 assumed
- No remediation actions were taken during verification. Read-only throughout.
