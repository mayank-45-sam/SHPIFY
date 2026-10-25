"""Edge case tests for shopify_csv_generator.py (development only)."""
import csv
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
PROG = HERE / "shopify_csv_generator.py"
# Written to the temp folder so a test run never leaves files behind in the
# project folder. Every run deletes the file first, so the checks below can
# never be skipped by accident.
OUT = Path(tempfile.gettempdir()) / "playbook_tees_edge.csv"

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if not condition else ""))
    if not condition:
        failures.append(name)


def read_out():
    """Return (header_index_map, body_rows) or (None, None) when nothing was written."""
    if not OUT.exists():
        return None, None
    with open(OUT, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None, None
    return {c: n for n, c in enumerate(rows[0])}, rows[1:]


def run(answers, args=("--force",)):
    if OUT.exists():
        OUT.unlink()
    proc = subprocess.run([sys.executable, str(PROG), "--output", str(OUT), *args],
                          input="\n".join(answers) + "\n",
                          capture_output=True, text=True)
    return proc


print("=== A. Two products, multiple images, blank SKUs ===")
answers = [
    "Test Tee One",                                  # title
    'Desc with, comma and "quotes"',                 # desc
    "",                                              # end desc
    "500",                                           # price
    "http://cdn.shopify.com/s/images/shopify_shirt.png",   # image 1
    "http://cdn.shopify.com/s/images/shopify_shirt.png?v=2",  # image 2
    "",                                              # end images
    "Vendor Co",                                     # vendor
    "S,M",                                           # sizes
    "3", "",                                         # qty, blank SKU
    "4", "",                                         # qty, blank SKU
    "Cricket, T-Shirt, Graphic Tee",                 # tags
    "active",                                        # status
    "n",                                             # no optional details
    "n",                                             # no 2nd product -> wait, need 2nd
]
# Second product
answers2 = [
    "Test Tee Two",
    "Second desc",
    "",
    "650",
    "http://cdn.shopify.com/s/images/shopify_shirt.png",
    "",
    "Vendor Co",
    "L,XL",
    "9", "T2-L",
    "7", "T2-XL",
    "Cricket",
    "draft",
    "n",
    "n",
]
# Reorder: product1 -> add another = y -> product2 -> add another = n
answers = [
    "Test Tee One",
    'Desc with, comma and "quotes"',
    "",
    "500",
    "http://cdn.shopify.com/s/images/shopify_shirt.png",
    "http://cdn.shopify.com/s/images/shopify_shirt.png?v=2",
    "",
    "Vendor Co",
    "S,M",
    "3", "",
    "4", "",
    "Cricket, T-Shirt, Graphic Tee",
    "active",
    "n",
    "y",
] + answers2

proc = run(answers)
check("two-product run exits 0", proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:])
i, b = read_out()
check("two-product run wrote a CSV", i is not None, str(OUT))
if i is not None:
    check("2 products x 2 variants + 1 extra image row = 5 rows", len(b) == 5, str(len(b)))
    check("two distinct handles",
          len({r[i["URL handle"]] for r in b}) == 2)
    check("extra image row has handle + image + position only",
          b[2][i["URL handle"]] == "test-tee-one"
          and b[2][i["Product image URL"]].endswith("?v=2")
          and b[2][i["Image position"]] == "2"
          and b[2][i["Option1 value"]] == ""
          and b[2][i["SKU"]] == ""
          and b[2][i["Price"]] == "",
          str(b[2]))
    check("blank SKUs stayed blank (not invented)",
          b[0][i["SKU"]] == "" and b[1][i["SKU"]] == "")
    check("second product SKUs written", b[3][i["SKU"]] == "T2-L" and b[4][i["SKU"]] == "T2-XL")
    check("second product is draft", b[3][i["Status"]] == "draft")
    check("second product row carries Title", b[3][i["Title"]] == "Test Tee Two")
    check("desc quotes round-tripped",
          b[0][i["Description"]] == 'Desc with, comma and "quotes"',
          repr(b[0][i["Description"]]))
    check("summary reports 2 products / 4 variants",
          "Products created : 2" in proc.stdout and "Variants created : 4" in proc.stdout,
          proc.stdout)

print()
print("=== B. Duplicate SKU across products is rejected ===")
answers = [
    "Dup Test", "d", "", "100",
    "http://cdn.shopify.com/s/images/shopify_shirt.png", "",
    "V", "S", "1", "SAME-SKU",
    "Cricket", "active", "n",
    "y",
    "Dup Test 2", "d", "", "100",
    "http://cdn.shopify.com/s/images/shopify_shirt.png", "",
    "V", "S", "1", "SAME-SKU",
    "Cricket", "active", "n",
    "n",
]
proc = run(answers)
check("duplicate SKU rejected", proc.returncode == 1, str(proc.returncode))
check("duplicate SKU message shown", "Duplicate SKU" in proc.stdout, proc.stdout[-800:])
check("no file written on duplicate SKU", not OUT.exists() or "SAME-SKU" not in OUT.read_text())

print()
print("=== C. Duplicate handle is rejected ===")
answers = [
    "Same Title", "d", "", "100",
    "http://cdn.shopify.com/s/images/shopify_shirt.png", "",
    "V", "S", "1", "A-S",
    "Cricket", "active", "n",
    "y",
    "Same Title", "d", "", "100",
    "http://cdn.shopify.com/s/images/shopify_shirt.png", "",
    "V", "S", "1", "B-S",
    "Cricket", "active", "n",
    "n",
]
proc = run(answers)
check("duplicate handle rejected", proc.returncode == 1, str(proc.returncode))
check("duplicate handle message shown", "Duplicate URL handle" in proc.stdout, proc.stdout[-800:])

print()
print("=== D. Bad inputs are rejected at the prompt ===")
answers = [
    "Bad Input Tee",
    "d", "",
    "-500",                       # negative price
    "abc",                        # non-numeric price
    "0",                          # zero price
    "750",                        # good price
    "not-a-url",                  # bad image
    "http://cdn.shopify.com/s/images/shopify_shirt.png",  # good image
    "",
    "V",
    "XXL",                        # bad size
    "M,M",                        # duplicate size
    "S,M",                        # good sizes
    "-4",                         # qty S -> rejected
    "2.5",                        # qty S -> rejected
    "0",                          # qty S -> accepted
    "",                           # sku S
    "5",                          # qty M -> accepted
    "SK-M",                       # sku M
    "Cricket", "published",       # bad status
    "active",
    "n",
    "n",
]
proc = run(answers)
check("run recovers from bad input and still succeeds", proc.returncode == 0,
      proc.stdout[-1500:] + proc.stderr[-1500:])
for expected in ["must be a number", "must be greater than 0", "must start with http://",
                 "Unsupported size", "Duplicate size", "cannot be negative",
                 "whole number", "Status must be one of"]:
    check(f"error message present: {expected!r}", expected in proc.stdout, proc.stdout[-1500:])
i, b = read_out()
check("recovered run wrote a CSV", i is not None, str(OUT))
if i is not None:
    check("recovered product has correct price", b[0][i["Price"]] == "750.00",
          b[0][i["Price"]])
    check("recovered product has correct sizes",
          [r[i["Option1 value"]] for r in b] == ["S", "M"],
          str([r[i["Option1 value"]] for r in b]))
    check("recovered product has zero stock where typed", b[0][i["Inventory quantity"]] == "0")

print()
print("=== E. Extra sizes / images are handled ===")
answers = [
    "Multi Image Tee", "d", "", "100",
    "http://cdn.shopify.com/s/images/shopify_shirt.png",
    "http://cdn.shopify.com/s/images/shopify_shirt.png?v=2",
    "http://cdn.shopify.com/s/images/shopify_shirt.png?v=3",
    "",
    "V", "XS,S,M,L,XL,2XL",
    "1", "", "2", "", "3", "", "4", "", "5", "", "6", "",
    "Cricket", "active", "n", "n",
]
proc = run(answers)
check("multi-image run exits 0", proc.returncode == 0, proc.stdout[-800:])
i, b = read_out()
check("multi-image run wrote a CSV", i is not None, str(OUT))
if i is not None:
    check("6 variants + 2 extra image rows = 8 rows", len(b) == 8, str(len(b)))
    positions = sorted(r[i["Image position"]] for r in b if r[i["Product image URL"]])
    check("image positions are 1,2,3", positions == ["1", "2", "3"], str(positions))
    check("image rows carry no variant data",
          all(r[i["Option1 value"]] == "" and r[i["Price"]] == "" and r[i["SKU"]] == ""
              for r in b if r[i["Image position"]] in ("2", "3")))

print()
print("=== F. --help works ===")
proc = subprocess.run([sys.executable, str(PROG), "--help"], capture_output=True, text=True)
check("--help exits 0", proc.returncode == 0)
check("--help mentions --demo", "--demo" in proc.stdout)
check("--help mentions --validate", "--validate" in proc.stdout)

if OUT.exists():
    OUT.unlink()
print()
print("=" * 60)
if failures:
    print(f"FAILED: {len(failures)}: {failures}")
    sys.exit(1)
print("ALL EDGE CASES PASSED")
