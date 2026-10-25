# Playbook Tees — online store

A complete, mobile-friendly storefront for graphic T-shirts, plus an admin area for
catalogue, orders and print-supplier handoff. Built with Flask, SQLite, Jinja2 and
vanilla CSS/JS — no build step, no npm, no bundler.

The folder also still contains the standalone **Shopify CSV generator** from earlier in
the project. That tool is documented separately in [`SHOPIFY_CSV.md`](SHOPIFY_CSV.md).

---

## Quick start (5 minutes)

```bash
cd C:\Users\mayan\OneDrive\Desktop\SHPIFY

python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt

python seed.py                  # loads your artwork + sample products
python app.py                   # http://127.0.0.1:5000
```

The site is then at **http://127.0.0.1:5000** and the admin area at
**http://127.0.0.1:5000/admin**.

Secrets live in a local `.env` file, which is never committed. Generate your own
admin password hash before going live - see [Configuration](#configuration). The
print supplier is left empty on purpose, so it keeps reporting "supplier not
connected" until you add credentials for it.

`python app.py` runs with the Flask debug server and auto-reload, so edits to the
Python files are picked up automatically. It is fine for local work; see
[Running it for real](#running-it-for-real) before putting it online.

### Minimum `.env` for a usable store

```ini
SECRET_KEY=<64 random hex characters>
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<paste the hash you generate>
```

Generate both values:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
```

Without `ADMIN_PASSWORD_HASH` the admin login always refuses the attempt — the
login page says so rather than letting anyone in.

---

## What the store does

**Customer side**

| Page | URL | What happens there |
|---|---|---|
| Home | `/` | Hero, category tiles, featured tees, size guide, ticker |
| Shop | `/shop` | All products, filters by category, size and price, search, sorting |
| Product | `/product/<slug>` | Gallery, size picker, quantity, live stock, add to cart |
| Cart | `/cart` | Edit quantity, remove lines, free-shipping progress, checkout button |
| Checkout | `/checkout` | Name, phone, email, address, PIN code, order notes, payment method |
| Pay | `/checkout/pay/<order>` | UPI QR with the exact amount, or the Razorpay window |
| Order status | `/checkout/order/<order>` | What was ordered, where it is, what happens next |
| Track order | `/track-order` | Order number + phone number lookup |
| Contact | `/contact` | Message form (logged, replies by email) |

**Admin side** (`/admin`, session login)

| Page | URL | What happens there |
|---|---|---|
| Dashboard | `/admin` | Sales today/this month, recent orders, low stock, setup checklist |
| Products | `/admin/products` | Search, filter by status, edit, duplicate, publish/unpublish |
| New / Edit product | `/admin/products/new`, `/admin/products/<id>` | Details, price, sizes, stock mode, images, alt text |
| Orders | `/admin/orders` | Filter by status and payment status, search |
| Order detail | `/admin/orders/<id>` | Full order, status changes, tracking number, push to supplier |
| Settings | `/admin/settings` | Store settings, payment mode, supplier status, copyable env summary |

---

## How it behaves on purpose

These are decisions, not accidents:

* **Money is stored in paise** (integers). No floating point rounding errors, ever.
  Prices always come from the database — the browser never tells the server what
  something costs.
* **Two stock modes.** `made_to_order` means every offered size is orderable and
  nothing is counted down (print-on-demand). `tracked` means each size has a real
  stock number and a size with 0 stock is disabled everywhere.
* **The server owns the cart.** The cart lives in the database, keyed by a session
  token. `localStorage` is only a mirror that re-syncs on load, so a cleared browser
  or a second device never loses or duplicates an order.
* **Free shipping over ₹999**, otherwise ₹49 flat. Both are configurable in `.env`.
* **CSRF on every POST**, including the cart and checkout API calls.
* **Prices, stock and product status are re-checked on the server** the moment the
  customer presses "Place order", not just when the page was rendered.
* **Maximums**: 10 per line, 25 items in the cart.
* **Draft products never appear** on the storefront, in search, in the API or in the
  cart — but they are visible in the admin area so you can finish them.

### Honest-by-default edges

Both external integrations are optional and the site never pretends they are working:

* **Payments (UPI and Razorpay).** With no method set up the store runs in **test
  mode**: the order is stored with `payment_status = 'test_mode'`, the checkout page
  says so in plain words, no payment window opens and no money moves. With the UPI
  QR on, an order is marked **paid** only after the shop owner confirms the money
  in the admin area; with Razorpay on, only after the backend verifies Razorpay's
  HMAC signature (and, on a separate path, the webhook signature). A customer
  saying "I have paid" is never treated as proof. There is no third way to mark
  an order paid.
* **Print supplier (Qikink).** With no credentials the admin area reports
  "supplier not connected" and the *Send to supplier* button refuses to pretend.
  Orders can still be placed on the website and fulfilled by hand.
* **Contact form.** There is no mail server wired up, so submissions are written to
  the application log and the visitor is told to expect an email reply.

---

## Configuration

Everything lives in `.env` (never committed; `.gitignore` already covers it). See
[`.env.example`](.env.example) for the annotated list.

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | generated into `instance/secret_key` | Session signing key. Set your own in production. |
| `FLASK_DEBUG` | `1` | Set to `0` in production. |
| `STORE_NAME` | `Playbook Tees` | Used in the header, titles and receipts. |
| `STORE_TAGLINE` | `Graphic tees for people who never sit out` | Hero copy. |
| `STORE_EMAIL` / `STORE_PHONE` / `STORE_ADDRESS` | placeholders | Shown in the footer and on the contact page. |
| `DEFAULT_TEE_PRICE` | `599` | Rupees. Price for newly seeded products. |
| `SHIPPING_FEE` | `49` | Rupees, flat. |
| `FREE_SHIPPING_THRESHOLD` | `999` | Rupees. Orders at or above this ship free. |
| `ADMIN_USERNAME` | `admin` | Admin login name. |
| `ADMIN_PASSWORD_HASH` | empty | Werkzeug password hash. |
| `UPI_ID` | empty | Your UPI id (VPA). Turns the UPI QR payment on. |
| `UPI_PAYEE_NAME` | empty | Name shown in the customer's UPI app. |
| `UPI_QR_IMAGE` | `img/payment/upi_qr.jpg` | Fallback QR image inside the static folder. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | empty | Optional: cards and auto-verified payments. |
| `RAZORPAY_WEBHOOK_SECRET` | empty | Verifies Razorpay webhooks. |
| `QIKINK_CLIENT_ID` / `QIKINK_CLIENT_SECRET` | empty | Supplier, live environment. |
| `QIKINK_SANDBOX_SECRET` | empty | Supplier, sandbox environment. |
| `QIKINK_SANDBOX_MODE` | `1` | `1` uses the sandbox catalogue, `0` uses live. |
| `QIKINK_API_BASE` | sandbox URL | Override if you need a different host. |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` behind HTTPS. |

### Turning on real payments

There are two ways to take real money, and the store uses whichever ones are
set up. `/api/health` tells you what is live right now:

```json
{"payment_mode": "live", "payment_methods": ["upi"]}
```

**UPI (a QR code) - the default.** This is the method most Indian shops use
and it needs no account with anyone.

1. Put your UPI id in `.env` as `UPI_ID` (and `UPI_PAYEE_NAME`).
2. Drop a QR image in `static/img/payment/` and point `UPI_QR_IMAGE` at it.
   The image you ship is the fallback; the checkout page draws its own QR code
   for each order.
3. Restart the server. `/api/health` now reports `"payment_mode": "live"`.

What happens when a customer pays:

* The checkout page offers the payment methods the store actually has. Nothing
  else is shown, so there is no "pay" button that goes nowhere.
* Placing an order creates the order with `payment_status = 'pending'` and
  `status = 'awaiting_payment'`. The customer lands on a page with a **QR code
  that already carries the exact amount** (`upi://pay?...&am=648`), so their
  UPI app opens with the amount filled in - no typing, no wrong amount. The
  order number travels with the payment as the note (`tn=`), which makes
  matching it to your bank statement easy.
* A "Open my UPI app instead" button is there for phones that dislike the
  camera.
* The customer can press **"I have made the payment"**. That sets the order to
  `submitted` - it is *not* proof of anything and the order is **not** marked
  paid. A QR code cannot be verified automatically, and the store will not
  pretend otherwise.
* You confirm the money in **Admin -> Orders**: press **Payment received**
  (with the UTR if you want it stored) and the order becomes `paid` and
  `confirmed`. If no money arrived, press **Not received** and the order stays
  unpaid so the customer can pay again.
* Only that button marks an order paid. Not the customer, not a screenshot.

**Razorpay (cards, netbanking, auto-verified payments) - optional.**

1. Create a Razorpay account and copy the **Key ID** and **Key Secret**.
2. Put them in `.env` as `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`.
3. Restart the server. The checkout page then offers the Razorpay window next
   to the UPI QR.
4. Recommended: set `RAZORPAY_WEBHOOK_SECRET` and add a webhook in the Razorpay
   dashboard for the `payment.captured` event pointing at
   `https://your-domain/checkout/api/webhooks/razorpay`, so orders are confirmed
   even if the customer's browser closes. The webhook needs a public HTTPS URL,
   so use a tunnel (ngrok, cloudflared) while you are testing.
5. Behind HTTPS set `SESSION_COOKIE_SECURE=1` and `FLASK_DEBUG=0`.

What happens once Razorpay is live:

* Placing an order creates **one** store order and **one** Razorpay order. The
  gateway order id is stored on the order row, so reloading the pay page or
  coming back later reuses the same Razorpay order instead of littering the
  dashboard with duplicates.
* The order page shows a **Pay now** button until the payment lands, so a
  customer who closed the window can finish paying from their order.
* An order is only marked **paid** after the backend verifies Razorpay's HMAC
  signature *and* checks that the gateway order in that signature is the one
  created for that order, so a signature from a cheap order cannot be replayed
  onto an expensive one.
* The webhook repeats that check independently, and refuses to confirm an order
  when the captured amount is not the order total.

With no method at all, everything still works end to end in labelled test
mode: no money can move and no screen claims a payment happened.

### Connecting the print supplier (Qikink)

Sandbox and live are **separate environments with separate product catalogues**, so
products must exist in the environment you point at.

```ini
QIKINK_SANDBOX_MODE=1
QIKINK_CLIENT_ID=...
QIKINK_SANDBOX_SECRET=...
QIKINK_API_BASE=https://sandbox.qikink.com
```

Then: *Admin → Settings → Check connection*. When it is green, an order's detail page
gets a **Send to supplier** button that pushes the order to
`POST {base}/api/order/create`. The payload is built by `supplier.build_supplier_payload()`
and the order number is truncated to 15 characters because the supplier rejects
anything longer.

Supplier SKUs come from the `supplier_sku` field on the product, so the SKU in
Qikink and the SKU in the store can differ.

---

### New-order alerts on your phone

Every order placed on the site can alert the shop owner. Set
`ORDER_ALERT_PHONE` to the number that should receive it, then pick one of the
free delivery paths:

* **ntfy (no account).** Install the ntfy app on the phone, subscribe to the
  topic in `NTFY_TOPIC`, and every order arrives as a push notification.
* **WhatsApp through CallMeBot (free).** Message `+34 623 78 64 49` on WhatsApp
  with `I allow callmebot to send me messages`. Paste the API key it replies
  with into `CALLMEBOT_API_KEY`; alerts then go to `ORDER_ALERT_PHONE`.
* **Your own SMS gateway.** Set `SMS_WEBHOOK_URL` to a URL that accepts
  `{"phone": "...", "message": "..."}` as JSON.

With none of them configured, the alert is only written to the application log
- the site does not pretend the owner was notified.

## Product images

`python seed.py` copies the mockups from the project root into
`static/img/products/`:

| File | Used as |
|---|---|
| `Front_1_c_1.jpg` / `Back_2_c_1.jpg` | Front and back shot of *Cricket Fan Graphic T-Shirt* |
| `Front_1_c_11.jpg` … `Back_2_c_72.jpg` | The eight Virat Kohli shots — front and back for designs 11, 16, 70 and 72 |
| `Front_1_c_1 (2).jpg` / `Back_2_c_1 (2).jpg` | The Spider-Man shots — copied in from the same folder, so Windows named them "... (2).jpg" and the seed renames them to `spiderman_front.jpg` / `spiderman_back.jpg` when it copies them into the library |
| `Front_1_c_1 (3).jpg` / `Back_2_c_1 (3).jpg` | The Dhoni shots, renamed to `dhoni_front.jpg` / `dhoni_back.jpg` the same way |
| `Front_1_c_1 (4).jpg` / `Back_2_c_1 (4).jpg` | The Dr Doom shots, renamed to `drdoom_front.jpg` / `drdoom_back.jpg` the same way |
| `Front_1_c_1 (5).jpg` / `Back_2_c_1 (5).jpg` | The white Virat colourway, renamed to `virat_white_front.jpg` / `virat_white_back.jpg` and listed as its own product |
| `Front_1_c_1 (6).jpg` / `Back_2_c_1 (6).jpg` | The blue jersey Virat colourway, renamed to `virat_blue_front.jpg` / `virat_blue_back.jpg` and listed as its own product |
| `Front_1_c_1 (7).jpg` / `Back_2_c_1 (7).jpg` | The Messi shots, renamed to `messi_front.jpg` / `messi_back.jpg` the same way |
| `default.jpg`, `default (2).jpg`, `default (3).jpg`, `default (4).jpg`, `default (5).jpg`, `default (6).jpg`, `default (7).jpg` | Left unattached on purpose — each is a byte-for-byte copy of the matching front shot, and the admin dashboard reports images on disk that are not attached yet |

Your originals are never moved or deleted.

**Eight published products:**

| Product | Price | Sizes | Images |
|---|---|---|---|
| *Virat Kohli Cricket Graphic T-Shirt* | ₹899 | XS–2XL | 8 (four front/back pairs) |
| *Virat Kohli White Cricket Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *Virat Kohli Blue Jersey Cricket Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *MS Dhoni Cricket Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *Messi Football Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *Spider-Man Superhero Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *Dr Doom Superhero Graphic T-Shirt* | ₹599 | XS–2XL | 2 |
| *Cricket Fan Graphic T-Shirt* | ₹599 (₹799 struck through) | XS–2XL | 2 |

The Virat Kohli, Virat Kohli White, Virat Kohli Blue Jersey, Dhoni, Messi,
Spider-Man and Dr Doom artwork was
confirmed by the owner, so all seven are published with a real price and their
gallery. *Cricket Fan Graphic T-Shirt* is still flagged `needs review`: the artwork
on that tee has not been identified, so the store does not pretend to know whose it
is. Open *Admin → Products*, confirm who or what the print is, rename it and clear
the flag.

The other two products (Rohit Sharma, Ronaldo) are drafts waiting for artwork.

To add artwork yourself: *Admin → Products → New*, upload a file or link one from the
image library, set the alt text, then publish. `seed.py` is safe to re-run: it
copies new images, updates the Virat Kohli product's price and images, and never
adds the same image twice.

---

## Layout of the project

```
app.py                 App factory, blueprint registration, template globals, error pages
config.py              Reads .env, exposes Config
db.py                  SQLite schema and query helpers
catalog.py             Products, variants, images
pricing.py             Totals, shipping rules, money formatting
cart.py                Server-side cart + the JSON cart API
orders.py              Orders, order events, status changes, stock decrement
payments.py            UPI links and QR codes, Razorpay orders and signature checks
supplier.py            Qikink: token, order payload, submission
security.py            CSRF helpers, admin login check
storefront.py          Public pages and the contact form
checkout.py            Checkout form, payment verification, order confirmation
admin.py               Admin area
seed.py                Copies images and creates the starter products
templates/             Jinja templates (templates/admin/ for the admin area)
static/css/styles.css  The whole design system
static/js/main.js      Cart drawer, API calls, admin helpers
static/img/products/   The image library
instance/store.db      The database (created automatically)
```

### Design

Near-black `#0C0C0D`, bone `#F3F1EC`, acid `#D8FF3E`. Anton for display type, Inter
for everything else. A scrolling ticker, a sticky header, a slide-out cart drawer,
and layouts that collapse to a single column on a phone. Reduced-motion and print
styles are included. Everything is plain CSS with custom properties, so changing the
palette means editing the `:root` block at the top of `styles.css`.

---

## Tests

Every suite is a standalone script that prints `PASS` / `FAIL` and exits non-zero on
failure. They use throwaway databases in your temp folder, so they never touch
`instance/store.db`.

```bash
python test_storefront.py      # public pages, cart, checkout, UPI flow, tracking, contact
python test_admin.py           # admin login, products, images, orders, payment confirmation
python test_integrations.py    # UPI links, payment signatures and the supplier payload
python test_shopify_csv_generator.py   # the CSV tool
python test_edge_cases.py              # the CSV tool
```

---

## Running it for real

The dev server is not a production server. For a real deployment:

1. Set `FLASK_DEBUG=0`, a strong `SECRET_KEY`, `SESSION_COOKIE_SECURE=1`.
2. Serve it with a WSGI server, for example:

   ```bash
   pip install gunicorn
   gunicorn "app:app" --bind 127.0.0.1:8000
   ```

   On Windows, `waitress` works: `waitress-serve --port=8000 app:app`.
3. Put nginx or Caddy in front for TLS and static files.
4. Keep `instance/` and `static/img/products/` on persistent storage.

`instance/store.db` is a single SQLite file — back it up by copying it.

---

## Known limits

* The contact form does not send email; it logs the message.
* Payment and supplier integrations cannot be exercised without real credentials, so
  those paths are covered by signature/payload tests rather than live calls.
* The seeded product's artwork identity is unknown, so the product stays flagged for
  review until you confirm it.
* Inventory is not reserved while an order is awaiting payment; the stock check
  happens when the order is placed and again when the payment is confirmed.

---

## The other tool in this folder

[`SHOPIFY_CSV.md`](SHOPIFY_CSV.md) documents `shopify_csv_generator.py` — the
beginner-friendly wizard that writes a Shopify-importable product CSV with one row
per size. It needs no packages and shares nothing with the website.
