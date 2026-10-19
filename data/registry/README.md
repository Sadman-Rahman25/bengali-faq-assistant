# BRAC product & policy registry — for the chatbot

Joined at query time to the manual text. The manual says what a product
IS; this registry says whether it is CURRENT. Never edit the manual
chunks from this.

All files: UTF-8 without BOM, Bengali NFC-normalised, header in row 1.
Read with `encoding="utf-8"` — no BOM handling needed.

## Files

| file | rows | key | what it holds |
|---|---|---|---|
| products.csv | 30 | product_id | status, availability, all three names, the availability sentence |
| insurers.csv | 5 | insurer_id | whether each still carries BRAC policies, and what it covers |
| rule_updates.csv | 26 | rule_id | manual rules checked by the field team |
| term_updates.csv | 2 | old_term | job titles and system names that changed |
| not_available.csv | 9 | item | what the bot must say it does not know |

## Values

- `status`: active · suspended (not offered now, may come back) · pilot
- `availability`: all_branches · limited_branches · demand_basis · situation_based · unspecified
- `verdict`: still_valid · changed · retired · not_confirmed
- `manual`: dabi · progoti · both
- `topic_terms` (rule_updates, not_available): semicolon-separated words a
  question about THIS row would contain. A row applies only when the question
  contains one, so a retrieved clause alone never triggers it.
  - Each word matches as a whole Bengali word, with or without a case or
    plural ending: `প্রিমিয়াম` matches প্রিমিয়ামের, but `মুনাফা` does not
    match inside মুনাফাভিত্তিক.
  - `product:<key>` matches a product by its key in `productmatch.NAMED`
    (e.g. `product:money_plant_savings`), with every spelling productmatch
    knows. Use it when officers ask by the product's name. Do not give two
    rows on the same clause the same product key: the topic words are then
    the only thing that tells them apart (rules 23 and 24).
  - An unknown key stops the registry loading, by design.

## Names

Every product has three names, and all three should match a question:

- `name_bn` — Bengali, used in answers
- `name_en` — official English, or the Banglish name where none exists
- `name_banglish` — romanised Bengali, so a question typed in Banglish matches

## How the chatbot uses it

1. A product is named in the question → look up `products.csv` and say
   `status_line_bn` FIRST. If `status` is suspended, say that before
   giving any terms.
2. A retrieved chunk's clause appears in `rule_updates.csv` with verdict
   changed, retired or not_confirmed → do not quote the manual's version
   as current. Say `current_rule_bn` and cite the clause as the outdated
   source.
3. An answer contains an `old_term` → use `new_term`, but keep citing the
   manual clause as written.
4. The question matches a `not_available.csv` topic → say `bot_says_bn`.
   Never fall back to the manual's old figure.

## Before loading

`product_id` values were assigned here. Reconcile them against the keys
in `products_catalogue.csv` and report any that do not match, rather than
creating new rows.
