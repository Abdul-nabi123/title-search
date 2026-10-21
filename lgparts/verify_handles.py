#!/usr/bin/env python3
"""Find and verify the live lgparts.com handle for each SKU, then write the UPDATE TSV.

Read-only: it only GETs public storefront JSON and never writes to the store.

Input  : titles.tsv  (sku <TAB> title), one row per SKU
Output : lg_titles_UPDATE.tsv  Handle | Command | Title   (verified rows only)
         held_back.tsv         SKU | Reason | Handles      (everything else)

Rules (per SKU):
  1. GET /search/suggest.json?q=<SKU>&resources[type]=product&resources[limit]=10
  2. GET /products/<handle>.js for every result; a handle matches only if one of
     its variants' "sku" equals the SKU exactly (surrounding whitespace ignored).
  3. 0 matches  -> held back: NOT LIVE - check Shopify admin
     >1 matches -> held back: list every matching handle for manual review
     1 match whose live title names a different part number -> held back for review
     1 clean match -> UPDATE row
  4. Every UPDATE row is fetched again and its SKU re-checked before the files are written.

Usage: python3 verify_handles.py [titles.tsv]
"""
import csv, json, re, sys, time, urllib.parse, urllib.request

STORE = "https://lgparts.com"
UA = {"User-Agent": "Mozilla/5.0 (title-verify; read-only)", "Accept": "application/json"}
# LG part numbers look like AKB76044714, 5220FR1251E, 0IPRPS5006C, 6174V-6002K
PART_RE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{6,}[A-Z0-9]\b")


def get_json(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** (i + 1)); continue
            raise
        except urllib.error.URLError:
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"gave up on {url}")


def search_handles(sku):
    q = urllib.parse.urlencode({"q": sku, "resources[type]": "product", "resources[limit]": "10"})
    data = get_json(f"{STORE}/search/suggest.json?{q}") or {}
    prods = data.get("resources", {}).get("results", {}).get("products", [])
    return list(dict.fromkeys(p["handle"] for p in prods if p.get("handle")))


def product(handle):
    return get_json(f"{STORE}/products/{urllib.parse.quote(handle)}.js")


def variant_skus(p):
    return [(v.get("sku") or "").strip() for v in (p or {}).get("variants", [])]


def other_part_numbers(title, sku):
    return sorted({t for t in PART_RE.findall((title or "").upper()) if t != sku.upper()})


def main(path="titles.tsv"):
    rows = [r for r in csv.reader(open(path, newline="", encoding="utf-8"), delimiter="\t") if r and r[0].strip()]
    if rows[0][0].strip().lower() == "sku":
        rows = rows[1:]
    titles = {}
    for sku, title in ((r[0].strip(), r[1].strip()) for r in rows):
        titles.setdefault(sku, title)  # duplicate SKUs processed once

    ok, held = [], []
    for n, (sku, title) in enumerate(titles.items(), 1):
        handles = search_handles(sku)
        matches = []
        for h in handles:
            p = product(h)
            if sku in variant_skus(p):
                matches.append((h, p.get("title", "")))
            time.sleep(0.3)
        if not matches:
            held.append((sku, "NOT LIVE - check Shopify admin", ""))
        elif len(matches) > 1:
            held.append((sku, "MULTIPLE MATCHES - manual review", " | ".join(h for h, _ in matches)))
        else:
            h, live_title = matches[0]
            others = other_part_numbers(live_title, sku)
            if others:
                held.append((sku, f"LIVE TITLE SHOWS DIFFERENT PART NUMBER ({', '.join(others)}): {live_title}", h))
            else:
                ok.append((h, "UPDATE", title, sku))
        print(f"[{n}/{len(titles)}] {sku}: {len(matches)} match(es)", file=sys.stderr)
        time.sleep(0.3)

    # Final re-check: every handle going into the TSV must still carry the SKU in its title.
    final = []
    for h, cmd, title, sku in ok:
        if not title.endswith(f" - {sku}"):
            held.append((sku, "TITLE DOES NOT END WITH SKU", h)); continue
        if sku in variant_skus(product(h)):
            final.append((h, cmd, title))
        else:
            held.append((sku, "FAILED RE-CHECK (SKU no longer on handle)", h))
        time.sleep(0.3)

    with open("lg_titles_UPDATE.tsv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["Handle", "Command", "Title"]); w.writerows(final)
    with open("held_back.tsv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["SKU", "Reason", "Handles"]); w.writerows(held)
    print(f"verified: {len(final)}  held back: {len(held)}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:])
