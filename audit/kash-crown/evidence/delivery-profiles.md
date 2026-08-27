# Evidence — Delivery Profiles (all 9)

Queried 2026-08-27 via `deliveryProfiles` with `rateProvider` and `methodConditions`. Read-only.

**Key finding: no method condition anywhere references order subtotal.** The only conditions in
the entire store are `TOTAL_WEIGHT` bands on the Apliiq profile. There is no $75 free-shipping
threshold configured.

| #   | Profile                                           | ID             | Default | US rate                                        |
| --- | ------------------------------------------------- | -------------- | ------- | ---------------------------------------------- |
| 1   | General profile                                   | `133939822904` | yes     | **$0.00** (Domestic "Standard", no conditions) |
| 2   | Printful: Tshirts (#PF-FRG1)                      | `134134563128` | no      | **$0.00**                                      |
| 3   | Printful: Tshirts (#PF-FRG1001)                   | `134234472760` | no      | **$0.00** (+ Express $9.99)                    |
| 4   | Printful: Snapbacks and Bucket hats (#PF-FRG1096) | `134234898744` | no      | $4.49 (+ Express $11.99)                       |
| 5   | Printful: Hoodies (#PF-FRG2)                      | `134234997048` | no      | $8.49                                          |
| 6   | Printful: Pin buttons (#PF-FRG35)                 | `134587842872` | no      | $4.50                                          |
| 7   | Printful: Stickers (#PF-FRG10)                    | `134588072248` | no      | $4.29                                          |
| 8   | Zendrop                                           | `134653772088` | no      | **$0.00** ("[Free Shipping]", worldwide zone)  |
| 9   | Apliiq Print On Demand                            | `135041614136` | no      | $5.96 → $157.99, weight-banded                 |

## General profile

- Domestic → "Standard" → **$0.00**, `methodConditions: []` — unconditional free shipping
- International → "International" → $11.99, no conditions

## Apliiq Print On Demand — US weight bands (partial)

| Weight (oz)      | Rate    |
| ---------------- | ------- |
| 0.01 – 7.9       | $5.96   |
| 7.91 – 11.9      | $6.92   |
| 11.91 – 15.9     | $8.85   |
| 15.91 – 31.9     | $11.80  |
| 31.91 – 47.9     | $15.65  |
| 47.91 – 63.9     | $18.85  |
| …                | …       |
| 799.85 – 1199.84 | $157.99 |

Zones: United States, Canada, Australia, Rest of World. Canada and Australia carry their own
weight ladders topping out at $179.95 and $123.40 respectively.

## Split-shipment exposure

Nine profiles means a mixed cart can draw rates from multiple profiles simultaneously, producing
several shipping charges and several parcels. Worked example: a cart with a Printful tee (free),
an Apliiq hoodie ($5.96+) and a Printful pin set ($4.50) bills three separate rates on an order
the storefront advertised as free shipping.
