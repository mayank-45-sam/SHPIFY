"""Verification harness for shopify_csv_generator.py (development only)."""
import csv
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PROG = HERE / "shopify_csv_generator.py"
SAMPLE = HERE / "sample_shopify_products.csv"

# Header taken from Shopify's live official template
# https://help.shopify.com/cdn/shopifycloud/product-knowledge/production/csv/product_template.csv
OFFICIAL = (
    "Title,URL handle,Description,Vendor,Product category,Type,Tags,"
    "Published on online store,Status,SKU,Variant Barcodes,Option1 name,Option1 value,"
    "Option1 Linked To,Option2 name,Option2 value,Option2 Linked To,Option3 name,"
    "Option3 value,Option3 Linked To,Price,Compare-at price,Cost per item,Charge tax,"
    "Tax code,Unit price total measure,Unit price total measure unit,"
    "Unit price base measure,Unit price base measure unit,Inventory tracker,"
    "Inventory quantity,Continue selling when out of stock,Weight value (grams),"
    "Weight unit for display,Requires shipping,Fulfillment service,Product image URL,"
    "Image position,Image alt text,Variant image URL,Gift card,SEO title,SEO description,"
    "Color (product.metafields.shopify.color-pattern),"
    "Google Shopping / Google product category,Google Shopping / Gender,"
    "Google Shopping / Age group,Google Shopping / Manufacturer part number (MPN),"
    "Google Shopping / Ad group name,Google Shopping / Ads labels,"
    "Google Shopping / Condition,Google Shopping / Custom product,"
    "Google Shopping / Custom label 0,Google Shopping / Custom label 1,"
    "Google Shopping / Custom label 2,Google Shopping / Custom label 3,"
    "Google Shopping / Custom label 4,Packed product length,Packed product width,"
    "Packed product height,Packed product dimension unit"
).split(",")

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


print("=== 1. Encoding / line endings ===")
raw = SAMPLE.read_bytes()
check("no UTF-8 BOM", not raw.startswith(b"\xfe\xff") and not raw.startswith(b"\xef\xbb\xbf"))
text = raw.decode("utf-8")
check("decodes as UTF-8", True)
check("LF line endings only (no CRLF)", b"\r\n" not in raw)
check("no bare CR", b"\r" not in raw)
check("file ends with newline", raw.endswith(b"\n"))

print()
print("=== 2. Header correctness ===")
with open(SAMPLE, newline="", encoding="utf-8") as fh:
    rows = list(csv.reader(fh))
header, body = rows[0], rows[1:]

# "Collection" is the one column Shopify explicitly allows you to ADD to an
# import file; it is intentionally absent from Shopify's own export template.
ADDABLE = {"Collection"}
check("every column exists in the official Shopify template (or is Collection)",
      all(col in OFFICIAL or col in ADDABLE for col in header),
      str([c for c in header if c not in OFFICIAL and c not in ADDABLE]))
official_positions = [OFFICIAL.index(c) for c in header if c in OFFICIAL]
check("column order follows the official template order",
      official_positions == sorted(official_positions))
check("no metafield columns", not any("metafields" in c.lower() for c in header))
check("no 'Linked To' columns", not any("linked to" in c.lower() for c in header))
check("'Title' present", "Title" in header)
check("'URL handle' present", "URL handle" in header)
check("'Option1 name'/'Option1 value' present",
      "Option1 name" in header and "Option1 value" in header)

print()
print("=== 3. Row structure ===")
width = len(header)
check("all rows have the same column count", all(len(r) == width for r in body),
      str([len(r) for r in body]))
idx = {c: i for i, c in enumerate(header)}
variant_rows = [r for r in body if r[idx["Option1 value"]].strip()]
check("one row per variant (6 sizes)", len(variant_rows) == 6, str(len(variant_rows)))
handles = {r[idx["URL handle"]] for r in body}
check("all rows share one URL handle", len(handles) == 1, str(handles))
check("Option1 name == 'Size' on every variant row",
      all(r[idx["Option1 name"]] == "Size" for r in variant_rows))
sizes = [r[idx["Option1 value"]] for r in variant_rows]
check("sizes are XS,S,M,L,XL,2XL",
      sizes == ["XS", "S", "M", "L", "XL", "2XL"], str(sizes))
check("no duplicate size variants", len(set(sizes)) == len(sizes))
check("only the first row carries product-level fields",
      all(r[idx["Title"]] == "" for r in body[1:]))
check("every row has a handle", all(r[idx["URL handle"]].strip() for r in body))

print()
print("=== 4. Value validity ===")
check("Status is lowercase 'active'", body[0][idx["Status"]] == "active")
check("Published on online store is TRUE", body[0][idx["Published on online store"]] == "TRUE")
check("Charge tax is TRUE", body[0][idx["Charge tax"]] == "TRUE")
check("Requires shipping is TRUE", body[0][idx["Requires shipping"]] == "TRUE")
check("Gift card is FALSE", body[0][idx["Gift card"]] == "FALSE")
check("Inventory tracker is 'shopify'",
      all(r[idx["Inventory tracker"]] == "shopify" for r in variant_rows))
check("Continue selling when out of stock is 'deny'",
      all(r[idx["Continue selling when out of stock"]] == "deny" for r in variant_rows))
check("Fulfillment service is 'manual'",
      all(r[idx["Fulfillment service"]] == "manual" for r in variant_rows))
check("Weight unit for display is 'g'",
      all(r[idx["Weight unit for display"]] == "g" for r in variant_rows))
check("Type is 'Graphic T-Shirt'", body[0][idx["Type"]] == "Graphic T-Shirt")
check("Tags are Cricket, T-Shirt, Graphic Tee",
      body[0][idx["Tags"]] == "Cricket, T-Shirt, Graphic Tee", body[0][idx["Tags"]])
check("Product category is a valid taxonomy breadcrumb",
      body[0][idx["Product category"]]
      == "Apparel & Accessories > Clothing > Clothing Tops > T-Shirts")
check("all prices are 2dp decimals",
      all(r[idx["Price"]].count(".") == 1 and len(r[idx["Price"]].split(".")[1]) == 2
          for r in variant_rows))
check("all quantities are non-negative integers",
      all(r[idx["Inventory quantity"]].isdigit() for r in variant_rows))
check("image URL is present on row 1", body[0][idx["Product image URL"]].startswith("http"))
check("Image position starts at 1", body[0][idx["Image position"]] == "1")

print()
print("=== 5. --validate mode ===")
proc = subprocess.run([sys.executable, str(PROG), "--validate", str(SAMPLE)],
                      capture_output=True, text=True)
check("--validate exits 0 on a good file", proc.returncode == 0, proc.stdout + proc.stderr)

print()
print("=== 6. --validate catches a bad file ===")
bad = HERE / "_bad_test.csv"
bad.write_text(
    "Handle,Title,Body (HTML),Size (product.metafields.shopify.size),Price\n"
    "x,P,desc,M,-5\n"
    "x,,,,M,10\n",
    encoding="utf-8",
)
proc = subprocess.run([sys.executable, str(PROG), "--validate", str(bad)],
                      capture_output=True, text=True)
out = proc.stdout
check("--validate exits 1 on a bad file", proc.returncode == 1)
check("--validate flags metafield column", "metafields" in out)
check("--validate flags non-official column", "not an official Shopify" in out)
check("--validate flags missing URL handle", "'URL handle' column is missing" in out)
bad.unlink()

print()
print("=== 7. Interactive run (piped stdin) ===")
answers = "\n".join([
    "Virat Kohli 100th Century Tee",          # title
    "A cotton tee, \"premium\" print.",       # description line 1
    "Soft combed cotton, 240 GSM.",           # description line 2
    "",                                       # end description
    "Rs 1,299.00",                            # price (grouped + currency prefix)
    "http://cdn.shopify.com/s/images/shopify_shirt.png",   # image 1
    "",                                       # no more images
    "Cricket Threads",                        # vendor
    "XS, S, M, L, XL, 2XL",                   # sizes
    "5", "VK100-XS",                          # qty + sku
    "6", "VK100-S",
    "7", "VK100-M",
    "7", "VK100-L",
    "5", "VK100-XL",
    "3", "VK100-2XL",
    "Cricket, T-Shirt, Graphic Tee, 2026",    # tags
    "draft",                                  # status
    "y",                                      # add optional details
    "",                                       # handle -> default
    "1599.00",                                # compare-at
    "400",                                    # cost per item
    "210",                                    # weight
    "Virat Kohli 100th Century Graphic Tee",  # seo title
    "Celebrate 100 centuries in style.",      # seo description
    "Cricket Tees",                           # collection
    "n",                                      # no price override
    "n",                                      # no second product
]) + "\n"
out_csv = Path(tempfile.gettempdir()) / "playbook_tees_interactive.csv"
if out_csv.exists():
    out_csv.unlink()
proc = subprocess.run([sys.executable, str(PROG), "--force", "--output", str(out_csv)],
                      input=answers, capture_output=True, text=True)
check("interactive run exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
check("the interactive run wrote a CSV", out_csv.exists(), str(out_csv))
if out_csv.exists():
    with open(out_csv, newline="", encoding="utf-8") as fh:
        r2 = list(csv.reader(fh))
    h2, b2 = r2[0], r2[1:]
    i2 = {c: i for i, c in enumerate(h2)}
    check("interactive: 6 variant rows", len(b2) == 6, str(len(b2)))
    check("interactive: 'Rs 1,299.00' parsed to 1299.00",
          b2[0][i2["Price"]] == "1299.00", b2[0][i2["Price"]])
    check("interactive: status draft", b2[0][i2["Status"]] == "draft")
    check("interactive: Collection column written",
          "Collection" in h2 and b2[0][i2["Collection"]] == "Cricket Tees")
    check("interactive: SKU kept per size",
          [r[i2["SKU"]] for r in b2] == ["VK100-XS", "VK100-S", "VK100-M",
                                         "VK100-L", "VK100-XL", "VK100-2XL"])
    check("interactive: description kept its newline + quotes",
          "\n" in b2[0][i2["Description"]] and '"' in b2[0][i2["Description"]])
    proc = subprocess.run([sys.executable, str(PROG), "--validate", str(out_csv)],
                          capture_output=True, text=True)
    check("interactive file passes --validate", proc.returncode == 0,
          proc.stdout + proc.stderr)
    out_csv.unlink(missing_ok=True)
    check("test run did not leave the CSV behind", not out_csv.exists())

print()
print("=== 8. Validation rules (unit) ===")
sys.path.insert(0, str(HERE))
import shopify_csv_generator as gen  # noqa: E402

def raises(fn, arg, *args, **kwargs):
    try:
        fn(arg, *args, **kwargs)
    except gen.ValidationError:
        return True
    return False

check("rejects negative price", raises(gen.parse_price, "-5"))
check("rejects zero price", raises(gen.parse_price, "0"))
check("rejects non-numeric price", raises(gen.parse_price, "abc"))
check("rejects negative inventory", raises(gen.parse_quantity, "-3"))
check("rejects non-integer inventory", raises(gen.parse_quantity, "3.5"))
check("rejects unsupported size 'XXL'", raises(gen.parse_sizes, "XXL"))
check("rejects duplicate sizes", raises(gen.parse_sizes, "M,M,L"))
check("rejects empty size list", raises(gen.parse_sizes, "  "))
check("rejects non-http image url", raises(gen.validate_image_url, "ftp://x/y.png"))
check("rejects image url with _small suffix",
      raises(gen.validate_image_url, "https://a.com/p_small.png"))
check("rejects invalid status", raises(gen.parse_status, "published"))
check("accepts price with Rs prefix", str(gen.parse_price("Rs 1,299.00")) == "1299.00")
check("accepts Indian grouping", str(gen.parse_price("1,29,999")) == "129999.00")
check("accepts lowercase sizes", gen.parse_sizes("xs, s") == ["XS", "S"])
check("accepts 2XL", gen.parse_sizes("2XL") == ["2XL"])
check("slugify handles punctuation",
      gen.slugify("Rohit Sharma's Sixes #1 Tee!") == "rohit-sharma-s-sixes-1-tee")

print()
print("=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
