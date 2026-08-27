# Evidence — Product Status (live query)

Queried 2026-08-27 by explicit product GID. Read-only.

## Contained products — all confirmed DRAFT

| Product ID       | Handle                                      | Title                                     | Status | Vendor |
| ---------------- | ------------------------------------------- | ----------------------------------------- | ------ | ------ |
| `10837647491384` | `gildan-heavyweight-tee-rapid-fulfillment`  | Gildan Heavyweight Tee Rapid Fulfillment  | DRAFT  | Apliiq |
| `10837545615672` | `gildan-heavy-blend-hoodie-speedy-delivery` | Gildan Heavy Blend Hoodie Speedy Delivery | DRAFT  | Apliiq |
| `10837860876600` | `tultex-womens-poly-rich-t-shirt`           | Tultex Womens Poly Rich T Shirt           | DRAFT  | Apliiq |
| `10837862318392` | `womens-premium-cotton-t-shirt`             | Women's Premium Cotton T Shirt            | DRAFT  | Apliiq |
| `10837862416696` | `womens-premium-cotton-t-shirt-1`           | Women's Premium Cotton T Shirt            | DRAFT  | Apliiq |

All five retain `vendor: Apliiq` — containment was status-only, as intended. Titles, handles and
copy are unchanged, so these are not publishable until rewritten.

## Built Under Pressure Hoodie

| Field       | Value                                                  |
| ----------- | ------------------------------------------------------ |
| ID          | `gid://shopify/Product/10837409661240`                 |
| Handle      | `kash-crown-built-under-pressure-hoodie-pigment-black` |
| Title       | KASH CROWN Built Under Pressure Hoodie — Pigment Black |
| Status      | ACTIVE                                                 |
| Vendor      | KASH CROWN                                             |
| productType | HOODIE                                                 |

### Sales channels — ISSUE

```
resourcePublications:
  Online Store        isPublished: true    <- intended
  Microsoft Copilot   isPublished: true    <- NOT AUTHORISED, still live
```

Outstanding. Requires manual unpublish in admin, or explicit approval to do it via API.
