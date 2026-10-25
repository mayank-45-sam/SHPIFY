"""Seed the store with the artwork that is already in this folder.

Run it once after cloning:

    python seed.py

What it does:

* copies the product mockups from the project root into static/img/products/
  (the originals are never moved or deleted),
* publishes one real product using the front and back shots, flagged for review
  because the artwork itself has not been identified yet,
* creates draft products for the other designs so artwork can be attached
  later from the admin area.

Running it twice is safe: existing products are left alone.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "img" / "products"

SOURCE_IMAGES = {
    "Front_1_c_1.jpg": "Front_1_c_1.jpg",
    "Back_2_c_1.jpg": "Back_2_c_1.jpg",
    "default.jpg": "default.jpg",
    "Front_1_c_11.jpg": "Front_1_c_11.jpg",
    "Back_2_c_11.jpg": "Back_2_c_11.jpg",
    "Front_1_c_16.jpg": "Front_1_c_16.jpg",
    "Back_2_c_16.jpg": "Back_2_c_16.jpg",
    "Front_1_c_70.jpg": "Front_1_c_70.jpg",
    "Back_2_c_70.jpg": "Back_2_c_70.jpg",
    "Front_1_c_72.jpg": "Front_1_c_72.jpg",
    "Back_2_c_72.jpg": "Back_2_c_72.jpg",
    # The Spider-Man set was copied in from the same folder, so Windows named
    # it "... (2).jpg". It is renamed on the way into the image library so it
    # cannot be confused with the cricket set.
    "Front_1_c_1 (2).jpg": "spiderman_front.jpg",
    "Back_2_c_1 (2).jpg": "spiderman_back.jpg",
    "default (2).jpg": "spiderman_default.jpg",
    # The Dhoni set was copied in the same way, so it is renamed on the way
    # into the image library too.
    "Front_1_c_1 (3).jpg": "dhoni_front.jpg",
    "Back_2_c_1 (3).jpg": "dhoni_back.jpg",
    "default (3).jpg": "dhoni_default.jpg",
    # The Dr Doom set came in the same way.
    "Front_1_c_1 (4).jpg": "drdoom_front.jpg",
    "Back_2_c_1 (4).jpg": "drdoom_back.jpg",
    "default (4).jpg": "drdoom_default.jpg",
    # A fifth Virat design, copied in the same way. It is the white colourway,
    # so it is listed as its own product at a different price.
    "Front_1_c_1 (5).jpg": "virat_white_front.jpg",
    "Back_2_c_1 (5).jpg": "virat_white_back.jpg",
    "default (5).jpg": "virat_white_default.jpg",
    # A sixth Virat design, copied in the same way. It is the blue jersey
    # colourway, so it is listed as its own product.
    "Front_1_c_1 (6).jpg": "virat_blue_front.jpg",
    "Back_2_c_1 (6).jpg": "virat_blue_back.jpg",
    "default (6).jpg": "virat_blue_default.jpg",
    # The Messi artwork came in the same way.
    "Front_1_c_1 (7).jpg": "messi_front.jpg",
    "Back_2_c_1 (7).jpg": "messi_back.jpg",
    "default (7).jpg": "messi_default.jpg",
    # Poster artwork. Each pair is the same export saved twice, so only one
    # copy is kept in the product gallery. The Ronaldo pair arrived inside a
    # zip, so it is extracted from there and stored as its own source file.
    "default (8).jpg": "spiderman_poster.jpg",
    "default (9).jpg": "kohli_poster.jpg",
    "ronaldo_poster.jpg": "ronaldo_poster.jpg",
}

#: Poster size labels and the price of each size, in rupees.
POSTER_SIZES = ("18x24 in", "24x36 in")
POSTER_PRICES = {"18x24 in": 250, "24x36 in": 500}

SPIDERMAN_POSTER_PRODUCT = {
    "name": "Spider-Man Poster",
    "description": (
        "Full-colour Spider-Man wall poster printed on thick matte paper.\n\n"
        "Available in 18x24 in (Rs 250) and 24x36 in (Rs 500). "
        "Rolled in a protective tube and dispatched in 3 to 5 working days."
    ),
    "category": "superheroes",
    "price": 250,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": POSTER_SIZES,
    "prices": POSTER_PRICES,
    "images": [("spiderman_poster.jpg", "Spider-Man poster")],
}

KOHLI_POSTER_PRODUCT = {
    "name": "Virat Kohli Poster",
    "description": (
        "Full-colour Virat Kohli wall poster printed on thick matte paper.\n\n"
        "Available in 18x24 in (Rs 250) and 24x36 in (Rs 500). "
        "Rolled in a protective tube and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 250,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": POSTER_SIZES,
    "prices": POSTER_PRICES,
    "images": [("kohli_poster.jpg", "Virat Kohli poster")],
}

RONALDO_POSTER_PRODUCT = {
    "name": "Cristiano Ronaldo Poster",
    "description": (
        "Full-colour Cristiano Ronaldo wall poster printed on thick matte paper.\n\n"
        "Available in 18x24 in (Rs 250) and 24x36 in (Rs 500). "
        "Rolled in a protective tube and dispatched in 3 to 5 working days."
    ),
    "category": "football",
    "price": 250,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": POSTER_SIZES,
    "prices": POSTER_PRICES,
    "images": [("ronaldo_poster.jpg", "Cristiano Ronaldo poster")],
}

# The white colourway is a separate product from the dark Virat designs.
VIRAT_WHITE_PRODUCT = {
    "name": "Virat Kohli White Cricket Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic, printed on a white base. Cut in a regular unisex fit "
        "and pre-shrunk so it keeps its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("virat_white_front.jpg", "Virat Kohli white cricket graphic tee, front view"),
        ("virat_white_back.jpg", "Virat Kohli white cricket graphic tee, back view"),
    ],
}

# The blue jersey colourway is a separate product from the white and dark
# Virat designs.
VIRAT_BLUE_PRODUCT = {
    "name": "Virat Kohli Blue Jersey Cricket Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic, printed on a white base. Cut in a regular unisex fit "
        "and pre-shrunk so it keeps its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("virat_blue_front.jpg", "Virat Kohli blue jersey cricket graphic tee, front view"),
        ("virat_blue_back.jpg", "Virat Kohli blue jersey cricket graphic tee, back view"),
    ],
}

# The Messi artwork was confirmed by the owner, so this product is published
# with a real price and the studio shots. It reuses the draft placeholder that
# was waiting for the artwork.
MESSI_PRODUCT = {
    "name": "Messi Football Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic, printed on a white base. Cut in a regular unisex fit "
        "and pre-shrunk so it keeps its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "football",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("messi_front.jpg", "Messi football graphic tee, front view"),
        ("messi_back.jpg", "Messi football graphic tee, back view"),
    ],
}

# The Dr Doom artwork was confirmed by the owner, so this product is published
# with a real price and the studio shots.
DRDOOM_PRODUCT = {
    "name": "Dr Doom Superhero Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic. Cut in a regular unisex fit and pre-shrunk so it keeps "
        "its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "superheroes",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("drdoom_front.jpg", "Dr Doom superhero graphic tee, front view"),
        ("drdoom_back.jpg", "Dr Doom superhero graphic tee, back view"),
    ],
}

# The Dhoni artwork was confirmed by the owner, so this product is published
# with a real price and the studio shots.
DHONI_PRODUCT = {
    "name": "MS Dhoni Cricket Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic. Cut in a regular unisex fit and pre-shrunk so it keeps "
        "its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("dhoni_front.jpg", "MS Dhoni cricket graphic tee, front view"),
        ("dhoni_back.jpg", "MS Dhoni cricket graphic tee, back view"),
    ],
}

# The Spider-Man artwork was confirmed by the owner, so this product is
# published with a real price and the studio shots.
SPIDERMAN_PRODUCT = {
    "name": "Spider-Man Superhero Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic. Cut in a regular unisex fit and pre-shrunk so it keeps "
        "its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "superheroes",
    "price": 599,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("spiderman_front.jpg", "Spider-Man superhero graphic tee, front view"),
        ("spiderman_back.jpg", "Spider-Man superhero graphic tee, back view"),
    ],
}

# The Virat Kohli artwork was confirmed by the owner, so this product is
# published with a real price and the full set of studio shots.
VIRAT_PRODUCT = {
    "name": "Virat Kohli Cricket Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic, cut in a regular unisex fit and pre-shrunk so it keeps "
        "its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 899,
    "compare_at": 0,
    "status": "published",
    "is_featured": True,
    "needs_review": False,
    "review_note": "",
    "sizes": ("XS", "S", "M", "L", "XL", "2XL"),
    "images": [
        ("Front_1_c_11.jpg", "Virat Kohli cricket graphic tee, front view, design 1"),
        ("Back_2_c_11.jpg", "Virat Kohli cricket graphic tee, back view, design 1"),
        ("Front_1_c_16.jpg", "Virat Kohli cricket graphic tee, front view, design 2"),
        ("Back_2_c_16.jpg", "Virat Kohli cricket graphic tee, back view, design 2"),
        ("Front_1_c_70.jpg", "Virat Kohli cricket graphic tee, front view, design 3"),
        ("Back_2_c_70.jpg", "Virat Kohli cricket graphic tee, back view, design 3"),
        ("Front_1_c_72.jpg", "Virat Kohli cricket graphic tee, front view, design 4"),
        ("Back_2_c_72.jpg", "Virat Kohli cricket graphic tee, back view, design 4"),
    ],
}

HERO_PRODUCT = {
    "name": "Cricket Fan Graphic T-Shirt",
    "description": (
        "Heavyweight 240 GSM cotton tee with a full chest print and a large "
        "back graphic. Cut in a regular unisex fit and pre-shrunk so it keeps "
        "its shape after every wash.\n\n"
        "Printed one at a time and dispatched in 3 to 5 working days."
    ),
    "category": "cricket",
    "price": 599,
    "compare_at": 799,
    "status": "published",
    "is_featured": True,
    "needs_review": True,
    "review_note": (
        "Artwork uploaded from the studio mockups. Confirm which player or "
        "team is on the chest print, then rename the product and clear this flag."
    ),
    "supplier_sku": "",
    "images": [
        ("Front_1_c_1.jpg", "Cricket fan graphic tee, front view"),
        ("Back_2_c_1.jpg", "Cricket fan graphic tee, back view"),
    ],
}

DRAFT_PRODUCTS = [
    {
        "name": "Rohit Sharma Cricket Graphic T-Shirt",
        "description": "Bold cricket graphic tee. Artwork to be attached.",
        "category": "cricket",
        "review_note": "Artwork not attached yet - upload the final print file.",
    },
    {
        "name": "Ronaldo Football Graphic T-Shirt",
        "description": "Football graphic tee. Artwork to be attached.",
        "category": "football",
        "review_note": "Artwork not attached yet - upload the final print file.",
    },
]


def copy_images() -> list[str]:
    """Copy the source mockups into the image library. Returns what was copied."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    for source_name, target_name in SOURCE_IMAGES.items():
        source = BASE_DIR / source_name
        target = UPLOAD_DIR / target_name
        if not source.exists():
            continue
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            shutil.copy2(source, target)
            copied.append(target_name)
    return copied


def _sync_images(catalog, product_id: int, images, verbose: bool = True) -> int:
    """Attach any missing images. Never adds the same file twice."""
    product = catalog.get_product(product_id)
    attached = {image["filename"] for image in (product or {}).get("images", [])}
    added = 0
    for filename, alt in images:
        if filename in attached:
            continue
        if not (UPLOAD_DIR / filename).exists():
            continue
        catalog.add_image(product_id, filename, alt)
        added += 1
        if verbose:
            print(f"  + image {filename}")
    return added


def _sync_product(catalog, spec, verbose: bool = True):
    """Create the product if it is missing, otherwise bring it back in line.

    Returns (product_id, created). The price, status and sizes always come
    from the spec, so re-running the seed keeps the store as described here.
    """
    existing = catalog.get_product_by_slug(
        catalog.slugify(spec["name"]), include_unpublished=True
    )

    if existing is None:
        product_id = catalog.create_product(
            name=spec["name"],
            description=spec["description"],
            category=spec["category"],
            price_cents=spec["price"] * 100,
            compare_at_cents=spec.get("compare_at", 0) * 100,
            status=spec["status"],
            stock_mode=spec.get("stock_mode", "made_to_order"),
            sizes=spec["sizes"],
            prices={
                size: spec.get("prices", {}).get(size, 0) * 100 for size in spec["sizes"]
            },
            is_featured=spec.get("is_featured", False),
            needs_review=spec.get("needs_review", False),
            review_note=spec.get("review_note", ""),
            supplier_sku=spec.get("supplier_sku", ""),
        )
        if verbose:
            print(f"  + {spec['name']} (Rs {spec['price']})")
        return product_id, True

    catalog.update_product(
        existing["id"],
        description=spec["description"],
        price_cents=spec["price"] * 100,
        compare_at_cents=spec.get("compare_at", 0) * 100,
        status=spec["status"],
        is_featured=spec.get("is_featured", False),
        needs_review=spec.get("needs_review", False),
        review_note=spec.get("review_note", ""),
    )
    catalog.set_sizes(
        existing["id"],
        spec["sizes"],
        {v["size"]: v["stock"] for v in existing.get("variants", [])},
        {v["size"]: v["sku"] for v in existing.get("variants", [])},
        {
            size: spec.get("prices", {}).get(size, 0) * 100 for size in spec["sizes"]
        },
    )
    if verbose:
        print(f"= {spec['name']} updated (Rs {spec['price']})")
    return existing["id"], False


def seed_app(app, verbose: bool = True) -> None:
    """Seed an existing application context. Used by seed.py and the tests."""
    import catalog

    with app.app_context():
        copied = copy_images()
        if verbose:
            print("Images in the library:")
            for name in copied:
                print(f"  + {name}")
            if not copied:
                print("  (already copied)")

        created = []

        product = catalog.get_product_by_slug(
            catalog.slugify(HERO_PRODUCT["name"]), include_unpublished=True
        )
        if product is None:
            product_id = catalog.create_product(
                name=HERO_PRODUCT["name"],
                description=HERO_PRODUCT["description"],
                category=HERO_PRODUCT["category"],
                price_cents=HERO_PRODUCT["price"] * 100,
                compare_at_cents=HERO_PRODUCT["compare_at"] * 100,
                status=HERO_PRODUCT["status"],
                stock_mode="made_to_order",
                sizes=("XS", "S", "M", "L", "XL", "2XL"),
                is_featured=HERO_PRODUCT["is_featured"],
                needs_review=HERO_PRODUCT["needs_review"],
                review_note=HERO_PRODUCT["review_note"],
                supplier_sku=HERO_PRODUCT["supplier_sku"],
            )
            for filename, alt in HERO_PRODUCT["images"]:
                if (UPLOAD_DIR / filename).exists():
                    catalog.add_image(product_id, filename, alt)
            created.append(HERO_PRODUCT["name"])
        elif verbose:
            print(f"= {HERO_PRODUCT['name']} already exists")

        for spec in (
            VIRAT_PRODUCT,
            VIRAT_WHITE_PRODUCT,
            VIRAT_BLUE_PRODUCT,
            SPIDERMAN_PRODUCT,
            DHONI_PRODUCT,
            DRDOOM_PRODUCT,
            MESSI_PRODUCT,
            SPIDERMAN_POSTER_PRODUCT,
            KOHLI_POSTER_PRODUCT,
            RONALDO_POSTER_PRODUCT,
        ):
            product_id, was_created = _sync_product(catalog, spec, verbose=verbose)
            if was_created:
                created.append(spec["name"])
            added = _sync_images(catalog, product_id, spec["images"], verbose=verbose)
            if added:
                if verbose:
                    print(f"  + {added} image(s) on {spec['name']}")
            elif verbose:
                print(f"= {spec['name']} already has all its images")

        for draft in DRAFT_PRODUCTS:
            if catalog.get_product_by_slug(
                catalog.slugify(draft["name"]), include_unpublished=True
            ):
                if verbose:
                    print(f"= {draft['name']} already exists")
                continue
            catalog.create_product(
                name=draft["name"],
                description=draft["description"],
                category=draft["category"],
                price_cents=app.config["DEFAULT_TEE_PRICE"] * 100,
                status="draft",
                stock_mode="made_to_order",
                sizes=("XS", "S", "M", "L", "XL", "2XL"),
                needs_review=True,
                review_note=draft["review_note"],
            )
            created.append(draft["name"])

        if verbose:
            print("\nCreated:")
            for name in created:
                print(f"  + {name}")


def seed() -> None:
    from app import create_app

    app = create_app()
    seed_app(app)

    print("\nOpen the admin area to attach artwork to the drafts:")
    print("  python app.py   then go to http://127.0.0.1:5000/admin")


if __name__ == "__main__":
    try:
        seed()
    except Exception as exc:  # pragma: no cover - command line convenience
        print(f"Seed failed: {exc}", file=sys.stderr)
        raise
