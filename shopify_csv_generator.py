#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shopify Product CSV Generator - Cricket Graphic T-Shirts
========================================================

Creates a UTF-8 CSV file that can be uploaded to Shopify with:

    Shopify Admin -> Products -> Import products -> Upload file

The file uses Shopify's current official product CSV column headers
(see https://help.shopify.com/en/manual/products/import-export/using-csv).

Design notes
------------
* One row per size variant. Rows of the same product share one "URL handle".
* Product level fields (Title, Description, Vendor, Tags, ...) are written on
  the first row of each product only, exactly like Shopify's own export.
* NO metafield columns are ever written. Category metafields such as
  ``product.metafields.shopify.size`` require metaobject handles/GIDs and
  are the usual cause of the "invalid shopify.size metafield" import error.
  Product category is written as a plain taxonomy breadcrumb instead.
* Nothing is invented: image URLs, SKUs and stock levels come from you.
  A blank SKU stays blank and a missing image URL is never fabricated.

Usage
-----
    python shopify_csv_generator.py
    python shopify_csv_generator.py --demo
    python shopify_csv_generator.py --output my_products.csv
    python shopify_csv_generator.py --validate shopify_products.csv

Author: built for a cricket graphic T-shirt dropshipping store.
Python 3.8+ - standard library only, no pip install required.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROGRAM_NAME = "Shopify Product CSV Generator"

DEFAULT_OUTPUT_FILE = "shopify_products.csv"
DEFAULT_DEMO_FILE = "sample_shopify_products.csv"

#: Sizes the shop actually stocks. Nothing else is accepted.
SUPPORTED_SIZES: Tuple[str, ...] = ("XS", "S", "M", "L", "XL", "2XL")

#: The single option used by every product.
OPTION_NAME = "Size"

#: Default Shopify "Type" label (a free-form label of your own).
DEFAULT_PRODUCT_TYPE = "Graphic T-Shirt"

#: Default tags applied to every product.
DEFAULT_TAGS: Tuple[str, ...] = ("Cricket", "T-Shirt", "Graphic Tee")

#: Valid product categories, taken from Shopify's Standard Product Taxonomy.
#: These are full category breadcrumbs, which are stable across taxonomy
#: releases (the numeric category IDs are not, so they are not used here).
VALID_PRODUCT_CATEGORIES: Tuple[str, ...] = (
    "Apparel & Accessories > Clothing > Clothing Tops > T-Shirts",
    "Apparel & Accessories > Clothing > Clothing Tops > Shirts",
    "Apparel & Accessories > Clothing > Clothing Tops > Tank Tops",
)

DEFAULT_PRODUCT_CATEGORY = VALID_PRODUCT_CATEGORIES[0]

#: Product statuses offered by this tool. Shopify also supports "archived",
#: which is deliberately not offered here because it hides a product entirely.
VALID_STATUSES: Tuple[str, ...] = ("active", "draft")

#: Shopify's official product CSV headers, in the exact order used by
#: Shopify's own template. Only columns this tool actually uses are listed.
CSV_COLUMNS: Tuple[str, ...] = (
    "Title",
    "URL handle",
    "Description",
    "Vendor",
    "Product category",
    "Type",
    "Tags",
    "Published on online store",
    "Status",
    "SKU",
    "Variant Barcodes",
    "Option1 name",
    "Option1 value",
    "Price",
    "Compare-at price",
    "Cost per item",
    "Charge tax",
    "Inventory tracker",
    "Inventory quantity",
    "Continue selling when out of stock",
    "Weight value (grams)",
    "Weight unit for display",
    "Requires shipping",
    "Fulfillment service",
    "Product image URL",
    "Image position",
    "Image alt text",
    "Variant image URL",
    "Gift card",
    "SEO title",
    "SEO description",
)

#: The only column Shopify allows you to ADD to a product CSV import file.
COLLECTION_COLUMN = "Collection"

#: Default weight for a printed cotton tee (grams). Editable in the program.
DEFAULT_WEIGHT_GRAMS = 200

#: Reference image used by --demo. It is a real, publicly reachable image URL
#: taken from Shopify's own published product template.
DEMO_IMAGE_URL = "http://cdn.shopify.com/s/images/shopify_shirt.png"

MAX_TAGS_PER_PRODUCT = 250
MAX_SEO_TITLE_LENGTH = 70
MAX_SEO_DESCRIPTION_LENGTH = 320
MAX_HANDLE_LENGTH = 255

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """Raised when user input fails a validation rule."""


def normalize_text(value: str) -> str:
    """Normalise line endings to LF and strip surrounding whitespace.

    Shopify expects LF-style line feeds. Converting CR/CRLF up front also
    guarantees that a stray ``\\r`` can never escape CSV quoting.
    """
    if value is None:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def slugify(text: str) -> str:
    """Build a Shopify-safe URL handle from a product title."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:MAX_HANDLE_LENGTH].strip("-")


def money(value: Optional[Decimal]) -> str:
    """Format a Decimal as a plain two-decimal monetary value (no symbol)."""
    if value is None:
        return ""
    return f"{value:.2f}"


def format_tags(tags: Sequence[str]) -> str:
    """Join tags the way Shopify expects them: comma separated in one cell."""
    return ", ".join(tags)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Variant:
    """A single size of a product."""

    size: str
    quantity: int
    sku: str = ""
    price: Optional[Decimal] = None
    image_url: str = ""


@dataclass
class Product:
    """A product with one option ("Size") and one variant per size."""

    title: str
    description: str
    vendor: str
    price: Decimal
    image_urls: List[str] = field(default_factory=list)
    variants: List[Variant] = field(default_factory=list)
    tags: List[str] = field(default_factory=lambda: list(DEFAULT_TAGS))
    status: str = "active"
    published: bool = True
    track_inventory: bool = True
    handle: str = ""
    product_category: str = DEFAULT_PRODUCT_CATEGORY
    product_type: str = DEFAULT_PRODUCT_TYPE
    compare_at_price: Optional[Decimal] = None
    cost_per_item: Optional[Decimal] = None
    weight_grams: int = DEFAULT_WEIGHT_GRAMS
    seo_title: str = ""
    seo_description: str = ""
    collection: str = ""

    def image_alt_text(self, position: int) -> str:
        """Build alt text for an image from real product data only."""
        if position == 1:
            return f"{self.title} - front view"
        return f"{self.title} - view {position}"


# ---------------------------------------------------------------------------
# Validation parsers (pure functions, easy to test)
# ---------------------------------------------------------------------------

_PLAIN_NUMBER_RE = re.compile(r"^\d+(\.\d{1,2})?$")
_GROUPED_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d{1,2})?$")
_INDIAN_GROUPED_RE = re.compile(r"^\d{1,2}(,\d{2})+(,\d{3})?(\.\d{1,2})?$")
_CURRENCY_PREFIXES = ("rs.", "rs", "inr", "₹")


def parse_price(
    raw: str,
    *,
    allow_blank: bool = False,
    field_name: str = "Price",
) -> Optional[Decimal]:
    """Parse an INR price. Rejects blanks (unless allowed), junk and negatives."""
    text = normalize_text(raw).lower()
    for prefix in _CURRENCY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break

    if not text:
        if allow_blank:
            return None
        raise ValidationError(f"{field_name} is required.")

    if _GROUPED_RE.match(text) or _INDIAN_GROUPED_RE.match(text):
        text = text.replace(",", "")

    if not _PLAIN_NUMBER_RE.match(text):
        raise ValidationError(
            f"{field_name} must be a number such as 799 or 799.50 "
            f"(you entered: {normalize_text(raw)!r})."
        )

    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise ValidationError(f"{field_name} must be a number.") from exc

    if value <= 0:
        raise ValidationError(
            f"{field_name} must be greater than 0 (you entered: {normalize_text(raw)!r})."
        )
    if value > Decimal("10000000"):
        raise ValidationError(f"{field_name} cannot be more than 10,000,000.")

    return value.quantize(Decimal("0.01"))


def parse_quantity(raw: str, *, field_name: str = "Inventory quantity") -> int:
    """Parse a stock quantity. Negative values are rejected."""
    text = normalize_text(raw)
    if text.startswith("-"):
        raise ValidationError(
            f"{field_name} cannot be negative (you entered: {text!r}). "
            "Enter 0 if you do not track stock for this size."
        )
    if not re.fullmatch(r"\d+", text):
        raise ValidationError(
            f"{field_name} must be a whole number, 0 or more (you entered: {text!r})."
        )
    return int(text)


def parse_sizes(raw: str) -> List[str]:
    """Parse a comma/space separated size list and validate every entry."""
    parts = [part.strip().upper() for part in re.split(r"[,\s/]+", raw) if part.strip()]
    if not parts:
        raise ValidationError(
            "Enter at least one size. Supported sizes: " + ", ".join(SUPPORTED_SIZES)
        )

    unknown = [size for size in dict.fromkeys(parts) if size not in SUPPORTED_SIZES]
    if unknown:
        raise ValidationError(
            f"Unsupported size(s): {', '.join(unknown)}. "
            f"Supported sizes are: {', '.join(SUPPORTED_SIZES)}."
        )

    seen: set = set()
    duplicates: List[str] = []
    for size in parts:
        if size in seen and size not in duplicates:
            duplicates.append(size)
        seen.add(size)
    if duplicates:
        raise ValidationError(
            f"Duplicate size(s): {', '.join(duplicates)}. Enter each size only once."
        )

    return parts


def parse_single_size(raw: str) -> str:
    """Validate a single size against the supported list."""
    size = normalize_text(raw).upper()
    if size not in SUPPORTED_SIZES:
        raise ValidationError(
            f"Unsupported size {size!r}. Supported sizes are: {', '.join(SUPPORTED_SIZES)}."
        )
    return size


def parse_status(raw: str) -> str:
    """Validate a product status (active / draft)."""
    status = normalize_text(raw).lower()
    if status not in VALID_STATUSES:
        raise ValidationError(
            f"Status must be one of: {', '.join(VALID_STATUSES)} (you entered: {raw!r})."
        )
    return status


def parse_tags(raw: str) -> List[str]:
    """Parse, clean and validate a tag list."""
    tags: List[str] = []
    for chunk in raw.split(","):
        tag = normalize_text(chunk)
        if not tag:
            continue
        if tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS_PER_PRODUCT:
        raise ValidationError(
            f"A product can have at most {MAX_TAGS_PER_PRODUCT} tags (you entered {len(tags)})."
        )
    return tags


def validate_image_url(raw: str) -> str:
    """Validate an image URL. Images must be publicly reachable over http(s)."""
    url = normalize_text(raw)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(
            "Image URL must start with http:// or https:// "
            f"(you entered: {url!r}). Upload your images somewhere public first."
        )
    if not parsed.netloc:
        raise ValidationError(f"Image URL is missing a domain (you entered: {url!r}).")
    if re.search(r"_(thumb|small|medium)\b", parsed.path, re.IGNORECASE):
        raise ValidationError(
            "Image URL must not contain '_thumb', '_small' or '_medium' in the file name. "
            "Shopify re-uploads the image, so use the original full-size file."
        )
    return url


def validate_handle(raw: str) -> str:
    """Validate a URL handle."""
    handle = normalize_text(raw).lower()
    if not handle:
        raise ValidationError("URL handle cannot be empty.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]*", handle):
        raise ValidationError(
            "URL handle may only use lowercase letters, numbers, hyphens and "
            "underscores (no spaces)."
        )
    if len(handle) > MAX_HANDLE_LENGTH:
        raise ValidationError(f"URL handle must be {MAX_HANDLE_LENGTH} characters or fewer.")
    return handle


def parse_weight(raw: str) -> int:
    """Parse a weight in grams."""
    text = normalize_text(raw)
    if not re.fullmatch(r"\d+", text):
        raise ValidationError(f"Weight must be a whole number of grams (you entered: {raw!r}).")
    weight = int(text)
    if weight <= 0:
        raise ValidationError("Weight must be greater than 0 grams.")
    return weight


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------


def read_input(message: str, default: Optional[str] = None) -> str:
    """Read one line of input. An empty line returns ``default`` when given."""
    label = f"{message} [{default}]" if default else message
    try:
        raw = input(f"{label}: ").strip()
    except EOFError as exc:
        raise ValidationError("Input stream closed unexpectedly.") from exc
    if not raw and default is not None:
        return default
    return raw


def ask_required(message: str, default: Optional[str] = None) -> str:
    """Ask until a non-empty answer is given."""
    while True:
        value = read_input(message, default)
        if value:
            return value
        print("  ! This field is required.")


def ask_validated(
    message: str,
    parser,
    *,
    default: Optional[str] = None,
    allow_blank: bool = False,
    blank_value=None,
):
    """Ask until the parser accepts the answer."""
    while True:
        value = read_input(message, default)
        try:
            if allow_blank and not value:
                return blank_value
            return parser(value)
        except ValidationError as exc:
            print(f"  ! {exc}")


def ask_yes_no(message: str, *, default: bool = False) -> bool:
    """Ask a yes/no question."""
    hint = "Y/n" if default else "y/N"
    while True:
        raw = read_input(f"{message} ({hint})").lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! Please answer with 'y' or 'n'.")


# ---------------------------------------------------------------------------
# CSV construction
# ---------------------------------------------------------------------------


def build_header(products: Sequence[Product]) -> List[str]:
    """Return the header row, adding the optional Collection column if used."""
    header = list(CSV_COLUMNS)
    if any(product.collection for product in products):
        header.append(COLLECTION_COLUMN)
    return header


def build_rows(products: Sequence[Product], header: Sequence[str]) -> List[Dict[str, str]]:
    """Convert products into Shopify CSV rows (one row per variant)."""
    rows: List[Dict[str, str]] = []

    for product in products:
        for index, variant in enumerate(product.variants):
            row = {column: "" for column in header}

            if index == 0:
                # Product level data goes on the first row only.
                row["Title"] = product.title
                row["Description"] = product.description
                row["Vendor"] = product.vendor
                row["Product category"] = product.product_category
                row["Type"] = product.product_type
                row["Tags"] = format_tags(product.tags)
                row["Published on online store"] = "TRUE" if product.published else "FALSE"
                row["Status"] = product.status
                row["Gift card"] = "FALSE"
                row["SEO title"] = product.seo_title or product.title
                row["SEO description"] = product.seo_description or product.description

            # Every row needs the handle so Shopify groups the variants.
            row["URL handle"] = product.handle

            # Variant data.
            row["SKU"] = variant.sku
            row["Option1 name"] = OPTION_NAME
            row["Option1 value"] = variant.size
            row["Price"] = money(variant.price if variant.price is not None else product.price)
            row["Compare-at price"] = money(product.compare_at_price)
            row["Cost per item"] = money(product.cost_per_item)
            row["Charge tax"] = "TRUE"
            if product.track_inventory:
                row["Inventory tracker"] = "shopify"
                row["Inventory quantity"] = str(variant.quantity)
            else:
                row["Inventory tracker"] = ""
                row["Inventory quantity"] = ""
            row["Continue selling when out of stock"] = "deny"
            row["Weight value (grams)"] = str(product.weight_grams)
            row["Weight unit for display"] = "g"
            row["Requires shipping"] = "TRUE"
            row["Fulfillment service"] = "manual"
            row["Variant image URL"] = variant.image_url

            if COLLECTION_COLUMN in header:
                row[COLLECTION_COLUMN] = product.collection

            # The first image rides along with the first variant row.
            if index == 0 and product.image_urls:
                row["Product image URL"] = product.image_urls[0]
                row["Image position"] = "1"
                row["Image alt text"] = product.image_alt_text(1)

            rows.append(row)

        # Remaining images go on their own rows: handle + image + position only.
        for position, url in enumerate(product.image_urls[1:], start=2):
            row = {column: "" for column in header}
            row["URL handle"] = product.handle
            row["Product image URL"] = url
            row["Image position"] = str(position)
            row["Image alt text"] = product.image_alt_text(position)
            rows.append(row)

    return rows


def validate_products(products: Sequence[Product]) -> None:
    """Cross-product validation: unique handles, unique SKUs, sane sizes."""
    if not products:
        raise ValidationError("There are no products to write.")

    seen_handles: Dict[str, str] = {}
    seen_skus: Dict[str, str] = {}

    for product in products:
        if not product.title:
            raise ValidationError("A product is missing its title.")
        if not product.handle:
            raise ValidationError(f"Product {product.title!r} is missing a URL handle.")
        if not product.variants:
            raise ValidationError(f"Product {product.title!r} has no size variants.")

        key = product.handle.lower()
        if key in seen_handles:
            raise ValidationError(
                f"Duplicate URL handle {product.handle!r} used by both "
                f"{seen_handles[key]!r} and {product.title!r}. Handles must be unique."
            )
        seen_handles[key] = product.title

        sizes = [variant.size for variant in product.variants]
        for size in sizes:
            if size not in SUPPORTED_SIZES:
                raise ValidationError(
                    f"Product {product.title!r} uses unsupported size {size!r}. "
                    f"Supported sizes: {', '.join(SUPPORTED_SIZES)}."
                )
        if len(set(sizes)) != len(sizes):
            raise ValidationError(
                f"Product {product.title!r} has duplicate size variants: {', '.join(sizes)}."
            )

        for variant in product.variants:
            if variant.quantity < 0:
                raise ValidationError(
                    f"Product {product.title!r}, size {variant.size}: "
                    "inventory quantity cannot be negative."
                )
            if variant.sku:
                sku_key = variant.sku.lower()
                if sku_key in seen_skus:
                    raise ValidationError(
                        f"Duplicate SKU {variant.sku!r} used by both "
                        f"{seen_skus[sku_key]!r} and {product.title} ({variant.size}). "
                        "Shopify rejects duplicate SKUs."
                    )
                seen_skus[sku_key] = f"{product.title} ({variant.size})"


def write_csv(path: str, header: Sequence[str], rows: Sequence[Dict[str, str]]) -> None:
    """Write the CSV as UTF-8 (no BOM) with LF line endings."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(header),
            restval="",
            extrasaction="raise",
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def self_check(path: str, header: Sequence[str], expected_rows: int) -> None:
    """Re-read the written file and verify it is structurally sound."""
    with open(path, "r", newline="", encoding="utf-8") as handle:
        raw = handle.read()

    if raw.startswith("\ufeff"):
        raise ValidationError("The CSV must not start with a UTF-8 BOM.")

    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        parsed = list(reader)

    if not parsed:
        raise ValidationError("The generated CSV is empty.")
    if parsed[0] != list(header):
        raise ValidationError("The generated CSV header does not match the expected header.")

    body = parsed[1:]
    if len(body) != expected_rows:
        raise ValidationError(
            f"The generated CSV has {len(body)} data rows, expected {expected_rows}."
        )

    for number, record in enumerate(body, start=2):
        if len(record) != len(header):
            raise ValidationError(
                f"Row {number} has {len(record)} fields, expected {len(header)}."
            )

    for column in header:
        if "metafields" in column.lower() or "linked to" in column.lower():
            raise ValidationError(
                f"Column {column!r} is a metafield column and must not be written."
            )


# ---------------------------------------------------------------------------
# Interactive collection
# ---------------------------------------------------------------------------


def read_description() -> str:
    """Read a multi-line description. An empty line ends the input."""
    print("Description (type your text; press Enter on an empty line when done):")
    lines: List[str] = []
    while True:
        try:
            raw = input("  > ")
        except EOFError:
            break
        if not raw.strip() or raw.strip() == ":end":
            break
        lines.append(raw)
    return normalize_text("\n".join(lines))


def collect_image_urls() -> List[str]:
    """Ask for one or more public image URLs."""
    urls: List[str] = []
    while True:
        number = len(urls) + 1
        raw = read_input(f"Image URL #{number}")
        if not raw:
            if number == 1:
                print("  ! At least one image URL is required.")
                continue
            break
        try:
            urls.append(validate_image_url(raw))
        except ValidationError as exc:
            print(f"  ! {exc}")
    return urls


def collect_variants(sizes: Sequence[str]) -> List[Variant]:
    """Ask for quantity and optional SKU for each size."""
    variants: List[Variant] = []
    for size in sizes:
        quantity = ask_validated(f"  Inventory quantity for size {size}", parse_quantity)
        sku = read_input(f"  SKU for size {size} (optional, press Enter to skip)")
        variants.append(Variant(size=size, quantity=quantity, sku=sku.strip()))
    return variants


def collect_optional_details(product: Product) -> None:
    """Ask the optional questions. Everything here can be skipped."""
    print()
    print("  Optional details (press Enter to accept the shown default):")

    product.handle = ask_validated(
        "  URL handle",
        validate_handle,
        default=slugify(product.title) or product.title.lower(),
    )

    product.compare_at_price = ask_validated(
        "  Compare-at price INR (blank for none)",
        lambda value: parse_price(value, allow_blank=True),
    )
    product.cost_per_item = ask_validated(
        "  Cost per item INR (blank for none)",
        lambda value: parse_price(value, allow_blank=True),
    )
    product.weight_grams = ask_validated(
        "  Weight in grams",
        parse_weight,
        default=str(DEFAULT_WEIGHT_GRAMS),
    )

    seo_title = ask_validated("  SEO title (blank for none)", lambda value: value, default="")
    product.seo_title = seo_title[:MAX_SEO_TITLE_LENGTH]

    seo_description = ask_validated("  SEO description (blank for none)", lambda value: value)
    product.seo_description = seo_description[:MAX_SEO_DESCRIPTION_LENGTH]

    product.collection = ask_validated(
        "  Collection name (blank for none)",
        lambda value: value,
    )

    if ask_yes_no("  Override the price for one specific size?", default=False):
        size = ask_validated("    Which size?", parse_single_size)
        price = ask_validated("    New price INR", parse_price)
        matched = False
        for variant in product.variants:
            if variant.size == size:
                variant.price = price
                matched = True
        if not matched:
            raise ValidationError(
                f"Size {size} is not one of this product's sizes "
                f"({', '.join(v.size for v in product.variants)})."
            )


def collect_product() -> Product:
    """Ask every question for one product and return it."""
    print()
    print("=" * 70)
    print("NEW PRODUCT")
    print("=" * 70)

    title = ask_required("Product title")
    description = read_description()
    price = ask_validated("Price INR", parse_price)
    image_urls = collect_image_urls()
    vendor = ask_required("Vendor name")
    sizes = ask_validated(
        "Available sizes (comma separated: " + ", ".join(SUPPORTED_SIZES) + ")",
        parse_sizes,
    )
    variants = collect_variants(sizes)

    tags = ask_validated(
        "Product tags (comma separated)",
        parse_tags,
        default=", ".join(DEFAULT_TAGS),
    )
    status = ask_validated("Product status (active / draft)", parse_status, default="active")

    product = Product(
        title=title,
        description=description,
        vendor=vendor,
        price=price,
        image_urls=image_urls,
        variants=variants,
        tags=tags,
        status=status,
        handle=slugify(title) or title.lower(),
    )

    if ask_yes_no("\nAdd optional details (handle, prices, weight, SEO, collection)?", default=False):
        collect_optional_details(product)

    return product


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(products: Sequence[Product], output_path: str) -> None:
    """Print a human readable summary of everything that was written."""
    total_variants = sum(len(product.variants) for product in products)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Products created : {len(products)}")
    print(f"Variants created : {total_variants}")
    print()

    for number, product in enumerate(products, start=1):
        print(f"{number}. {product.title}")
        print(f"   Status      : {product.status} ({'published' if product.published else 'not published'})")
        print(f"   URL handle  : {product.handle}")
        print(f"   Vendor      : {product.vendor}")
        print(f"   Type        : {product.product_type}")
        print(f"   Category    : {product.product_category or '(set in Shopify)'}")
        print(f"   Tags        : {', '.join(product.tags)}")
        print(f"   Base price  : INR {money(product.price)}")
        print(f"   Images      : {len(product.image_urls)}")
        print(f"   Variants    : {len(product.variants)}")
        for variant in product.variants:
            price = variant.price if variant.price is not None else product.price
            sku_text = variant.sku if variant.sku else "(no SKU - blank on purpose)"
            stock = variant.quantity if product.track_inventory else "not tracked"
            print(
                f"      - {variant.size:<4} price INR {money(price):>8}   "
                f"stock: {stock:<13} SKU: {sku_text}"
            )
        print()

    print("=" * 70)
    print(f"Output file: {output_path}")
    print("=" * 70)
    print()
    print("Next steps:")
    print("  1. Open the file in Excel / Google Sheets to double-check it.")
    print("  2. Shopify Admin -> Products -> Import products -> Upload file")
    print("  3. Upload the CSV, then review the import preview before confirming.")
    print()
    print("IMPORTANT: importing a CSV only creates the products in your store.")
    print("It does NOT connect them to a print-on-demand supplier and it does NOT")
    print("fulfil orders automatically. You still have to install and configure a")
    print("supplier app (Printful, Printify, Gelato, ...) and switch on order")
    print("syncing and fulfilment in that app.")
    print()


# ---------------------------------------------------------------------------
# CSV validation mode
# ---------------------------------------------------------------------------


def validate_existing_csv(path: str) -> int:
    """Check an existing CSV for the problems that break Shopify imports."""
    problems: List[str] = []

    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except FileNotFoundError:
        print(f"ERROR: file not found: {path}")
        return 1
    except UnicodeDecodeError:
        print(f"ERROR: {path} is not valid UTF-8. Re-save the file as UTF-8.")
        return 1

    if not rows:
        print(f"ERROR: {path} is empty.")
        return 1

    header = rows[0]
    body = rows[1:]

    allowed = set(CSV_COLUMNS) | {COLLECTION_COLUMN}
    for column in header:
        if "metafields" in column.lower():
            problems.append(
                f"Column {column!r} is a metafield column. Category metafields such as "
                "product.metafields.shopify.size need metaobject handles and are the usual "
                "cause of the 'invalid shopify.size metafield' error. Remove the column."
            )
        elif "linked to" in column.lower():
            problems.append(
                f"Column {column!r} links an option to a metafield. Remove it and use a "
                "plain option name / option value instead."
            )
        elif column not in allowed:
            problems.append(
                f"Column {column!r} is not an official Shopify product CSV column."
            )

    if "Title" not in header:
        problems.append("The required 'Title' column is missing.")
    if "URL handle" not in header:
        problems.append("The 'URL handle' column is missing (required for products with variants).")

    for number, record in enumerate(body, start=2):
        if len(record) != len(header):
            problems.append(
                f"Row {number} has {len(record)} fields but the header has {len(header)}."
            )

    index_of = {name: position for position, name in enumerate(header)}
    handles: Dict[str, List[Tuple[int, str]]] = {}
    skus: Dict[str, int] = {}

    for number, record in enumerate(body, start=2):
        if len(record) != len(header):
            continue

        def cell(name: str) -> str:
            position = index_of.get(name)
            if position is None or position >= len(record):
                return ""
            return record[position].strip()

        handle = cell("URL handle")
        if handle:
            handles.setdefault(handle.lower(), []).append((number, cell("Option1 value")))

        sku = cell("SKU")
        if sku:
            key = sku.lower()
            if key in skus:
                problems.append(
                    f"Row {number}: SKU {sku!r} is already used on row {skus[key]}."
                )
            else:
                skus[key] = number

        for size_column in ("Option1 value",):
            value = cell(size_column)
            if value and value.upper() not in SUPPORTED_SIZES:
                problems.append(
                    f"Row {number}: option value {value!r} is not one of "
                    f"{', '.join(SUPPORTED_SIZES)}."
                )

        price = cell("Price")
        if price:
            try:
                if Decimal(price) < 0:
                    problems.append(f"Row {number}: price {price!r} is negative.")
            except InvalidOperation:
                problems.append(f"Row {number}: price {price!r} is not a number.")

        quantity = cell("Inventory quantity")
        if quantity:
            if not re.fullmatch(r"\d+", quantity):
                problems.append(
                    f"Row {number}: inventory quantity {quantity!r} is not a whole number."
                )

    for handle, entries in handles.items():
        values = [value for _, value in entries if value]
        if len(set(values)) != len(values):
            problems.append(
                f"Handle {handle!r} repeats an option value: "
                f"{', '.join(sorted(set(v for v in values if values.count(v) > 1)))}."
            )

    print(f"Checking: {path}")
    print(f"Columns : {len(header)}")
    print(f"Rows    : {len(body)}")
    if problems:
        print()
        print(f"FOUND {len(problems)} PROBLEM(S):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print()
    print("OK - no problems found. This file looks importable.")
    return 0


# ---------------------------------------------------------------------------
# Demo product
# ---------------------------------------------------------------------------


def demo_product() -> Product:
    """Build the sample product used by --demo. All values are example data."""
    sizes = list(SUPPORTED_SIZES)
    demo_quantities = {"XS": 10, "S": 15, "M": 20, "L": 20, "XL": 15, "2XL": 8}
    demo_skus = {
        "XS": "RS-SIXES-XS",
        "S": "RS-SIXES-S",
        "M": "RS-SIXES-M",
        "L": "RS-SIXES-L",
        "XL": "RS-SIXES-XL",
        "2XL": "RS-SIXES-2XL",
    }

    variants = [
        Variant(
            size=size,
            quantity=demo_quantities[size],
            sku=demo_skus[size],
        )
        for size in sizes
    ]

    title = "Rohit Sharma Sixes Graphic T-Shirt"
    description = (
        "Show your cricket passion with this bold graphic tee.\n"
        'Inspired by the "Hitman", it features a high-impact cricket bat and ball '
        "graphic print.\n"
        'Made from 100% combed cotton, "240 GSM", with a crew neck and short sleeves.'
    )

    return Product(
        title=title,
        description=description,
        vendor="Cricket Threads",
        price=Decimal("799.00"),
        image_urls=[DEMO_IMAGE_URL],
        variants=variants,
        tags=list(DEFAULT_TAGS),
        status="active",
        handle=slugify(title),
        product_category=DEFAULT_PRODUCT_CATEGORY,
        product_type=DEFAULT_PRODUCT_TYPE,
        compare_at_price=Decimal("1299.00"),
        cost_per_item=Decimal("350.00"),
        weight_grams=200,
        seo_title="Rohit Sharma Inspired Graphic Cricket T-Shirt",
        seo_description=(
            "Bold cricket graphic T-shirt for fans, printed on 240 GSM combed cotton. "
            "Available in XS to 2XL."
        ),
        collection="Cricket Tees",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def print_banner() -> None:
    print()
    print("=" * 70)
    print(f"  {PROGRAM_NAME}")
    print("  Cricket graphic T-shirts -> Shopify product import CSV")
    print("=" * 70)


def build_and_write(products: List[Product], output_path: str) -> None:
    """Validate, build, write and self-check the CSV."""
    validate_products(products)
    header = build_header(products)
    rows = build_rows(products, header)

    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)

    write_csv(output_path, header, rows)
    self_check(output_path, header, len(rows))


def confirm_overwrite(path: str, force: bool) -> bool:
    """Return True when it is safe to overwrite the output file."""
    if force or not os.path.exists(path):
        return True
    print()
    print(f"! The file {path} already exists.")
    if not sys.stdin.isatty():
        print("  Overwriting it (non-interactive session).")
        return True
    return ask_yes_no("  Overwrite it?", default=False)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="shopify_csv_generator.py",
        description=(
            "Generate a Shopify product import CSV for cricket graphic T-shirts. "
            "Run without arguments for the interactive wizard."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python shopify_csv_generator.py\n"
            "  python shopify_csv_generator.py --demo\n"
            "  python shopify_csv_generator.py --output cricket_tees.csv\n"
            "  python shopify_csv_generator.py --validate shopify_products.csv\n"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output CSV file (default: shopify_products.csv, or "
        "sample_shopify_products.csv with --demo).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate the built-in sample product without asking any questions.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file without asking.",
    )
    parser.add_argument(
        "--validate",
        metavar="FILE",
        default=None,
        help="Check an existing CSV for problems that break Shopify imports.",
    )

    args = parser.parse_args(argv)

    if args.validate:
        return validate_existing_csv(args.validate)

    print_banner()

    if args.demo:
        output_path = args.output or DEFAULT_DEMO_FILE
        products = [demo_product()]
        print()
        print("Demo mode: one example product with placeholder data.")
        print("Replace the image URL, SKUs, stock and prices before importing.")
    else:
        output_path = args.output or DEFAULT_OUTPUT_FILE
        products = []
        while True:
            products.append(collect_product())
            if not ask_yes_no("\nAdd another product to the same file?", default=False):
                break

    if not confirm_overwrite(output_path, args.force):
        print()
        print("Nothing was written.")
        return 0

    try:
        build_and_write(products, output_path)
    except ValidationError as exc:
        print()
        print(f"ERROR: {exc}")
        return 1
    except OSError as exc:
        print()
        print(f"ERROR: could not write {output_path}: {exc}")
        return 1

    print_summary(products, os.path.abspath(output_path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        print("Cancelled. No file was written.")
        sys.exit(1)
    except ValidationError as error:  # pragma: no cover - top level guard
        print(f"ERROR: {error}")
        sys.exit(1)
