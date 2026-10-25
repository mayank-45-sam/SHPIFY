/* ==========================================================================
   Playbook Tees - front-end behaviour
   Vanilla JS, no dependencies. The server is always the source of truth for
   prices and stock; this file only keeps the interface in sync with it.
   ========================================================================== */
(function () {
  "use strict";

  /* ---------------------------------------------------------------- utils */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const csrfToken = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  };

  const CART_KEY = "pt_cart";

  function readLocalCart() {
    try {
      const raw = localStorage.getItem(CART_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      return [];
    }
  }

  function writeLocalCart(items) {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(items || []));
    } catch (err) {
      /* private mode - the server cart still works */
    }
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: {} }, options || {});
    opts.headers = Object.assign({ "X-CSRF-Token": csrfToken() }, opts.headers);
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const response = await fetch(path, opts);
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = { ok: false, error: "The server sent an unexpected response." };
    }
    if (!response.ok) {
      const message = (data && data.error) || "Something went wrong. Please try again.";
      const error = new Error(message);
      error.payload = data;
      throw error;
    }
    return data;
  }

  const rupees = (cents) => {
    const amount = cents / 100;
    const body = Number.isInteger(amount)
      ? amount.toLocaleString("en-IN")
      : amount.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return "₹" + body;
  };

  const escapeHtml = (value) =>
    String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  /* --------------------------------------------------------------- toasts */
  function toast(title, body, kind) {
    const host = $("[data-toasts]");
    if (!host) return;
    const node = document.createElement("div");
    node.className = "toast" + (kind === "error" ? " toast--error" : "");
    node.innerHTML =
      '<div class="toast__title">' + escapeHtml(title) + "</div>" +
      (body ? '<div class="toast__body">' + escapeHtml(body) + "</div>" : "");
    host.appendChild(node);
    window.setTimeout(() => {
      node.classList.add("is-out");
      window.setTimeout(() => node.remove(), 220);
    }, 3600);
  }

  /* ------------------------------------------------------------ nav + head */
  function initNav() {
    const toggle = $("[data-nav-toggle]");
    const list = $("#mobile-nav");
    if (!toggle || !list) return;

    toggle.addEventListener("click", () => {
      const open = list.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });

    document.addEventListener("click", (event) => {
      if (!list.classList.contains("is-open")) return;
      if (list.contains(event.target) || toggle.contains(event.target)) return;
      list.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    });

    window.addEventListener("resize", () => {
      if (window.innerWidth > 860 && list.classList.contains("is-open")) {
        list.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  function initStickyHeader() {
    const header = $("#site-header");
    if (!header) return;
    const onScroll = () => header.classList.toggle("is-stuck", window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function initFlashDismiss() {
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-dismiss]");
      if (!button) return;
      const flash = button.closest(".flash");
      if (flash) flash.remove();
    });
  }

  /* ----------------------------------------------------------------- cart */
  const Cart = {
    drawer: null,
    backdrop: null,
    body: null,
    foot: null,
    lastPayload: null,

    init() {
      this.drawer = $("[data-cart-drawer]");
      this.backdrop = $("[data-drawer-backdrop]") || $("[data-cart-backdrop]");
      this.body = $("[data-cart-body]");
      this.foot = $("[data-cart-foot]");
      if (!this.drawer) return;

      document.addEventListener("click", (event) => {
        if (event.target.closest("[data-cart-open]")) {
          event.preventDefault();
          this.open();
        }
        if (event.target.closest("[data-cart-close]")) this.close();
      });

      this.backdrop && this.backdrop.addEventListener("click", () => this.close());
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && this.drawer.classList.contains("is-open")) this.close();
      });

      // Buttons that jump straight to the cart page.
      $$("[data-cart-checkout]").forEach((link) => {
        link.addEventListener("click", () => this.close());
      });
    },

    open() {
      if (!this.drawer) return;
      this.drawer.classList.add("is-open");
      this.drawer.setAttribute("aria-hidden", "false");
      this.backdrop && this.backdrop.classList.add("is-open");
      if (this.backdrop) this.backdrop.hidden = false;
      document.body.classList.add("is-locked");
      this.refresh();
    },

    close() {
      if (!this.drawer) return;
      this.drawer.classList.remove("is-open");
      this.drawer.setAttribute("aria-hidden", "true");
      this.backdrop && this.backdrop.classList.remove("is-open");
      if (this.backdrop) window.setTimeout(() => { this.backdrop.hidden = true; }, 260);
      document.body.classList.remove("is-locked");
    },

    async refresh() {
      try {
        const payload = await api("/api/cart");
        this.render(payload);
      } catch (err) {
        this.body.innerHTML = '<p class="card__note">We could not load your cart.</p>';
      }
    },

    render(payload) {
      this.lastPayload = payload;
      const lines = payload.lines || [];
      const totals = payload.totals || {};

      $$("[data-cart-count]").forEach((node) => {
        const count = payload.item_count || 0;
        node.textContent = node.hasAttribute("aria-live") ? count : "(" + count + ")";
        if (node.hasAttribute("data-empty")) {
          node.setAttribute("data-empty", count === 0 ? "true" : "false");
        }
      });

      if (!this.body) return;

      if (!lines.length) {
        this.body.innerHTML =
          '<div class="empty">' +
          '<div class="empty__icon"><svg viewBox="0 0 24 24"><path d="M6 7h12l-1 13H7L6 7z"/><path d="M9 7a3 3 0 0 1 6 0"/></svg></div>' +
          "<h3>Your cart is empty</h3>" +
          "<p>Pick a design, choose your size and it will show up right here.</p>" +
          "</div>";
        if (this.foot) this.foot.hidden = true;
        return;
      }

      this.body.innerHTML = lines.map((line) => this.lineHtml(line)).join("");
      if (this.foot) this.foot.hidden = false;

      const subtotal = $("[data-cart-subtotal]", this.foot);
      const shippingValue = $("[data-cart-shipping-value]", this.foot);
      const total = $("[data-cart-total]", this.foot);
      const shippingNote = $("[data-cart-shipping]", this.foot);

      if (subtotal) subtotal.textContent = rupees(totals.subtotal_cents || 0);
      if (shippingValue) shippingValue.textContent =
        totals.shipping_cents ? rupees(totals.shipping_cents) : "Free";
      if (total) total.textContent = rupees(totals.total_cents || 0);
      if (shippingNote) shippingNote.textContent = totals.shipping_message || "";
    },

    lineHtml(line) {
      const image = line.image_url
        ? '<img src="' + escapeHtml(line.image_url) + '" alt="' + escapeHtml(line.name) + '" loading="lazy">'
        : "";
      return (
        '<div class="cart-line" data-cart-line="' + line.cart_id + '">' +
        '<div class="cart-line__media">' + image + "</div>" +
        '<div class="cart-line__info">' +
        '<div class="cart-line__name">' + escapeHtml(line.name) + "</div>" +
        '<div class="cart-line__meta">Size ' + escapeHtml(line.size) + "</div>" +
        (line.issue
          ? '<div class="cart-line__issue">' + escapeHtml(line.issue) + "</div>"
          : "") +
        '<div class="cart-line__row">' +
        '<div class="qty">' +
        '<button type="button" data-cart-dec="' + line.cart_id + '" aria-label="Decrease quantity">&minus;</button>' +
        "<output>" + line.quantity + "</output>" +
        '<button type="button" data-cart-inc="' + line.cart_id + '" aria-label="Increase quantity">+</button>' +
        "</div>" +
        '<div class="cart-line__price">' + escapeHtml(line.line_total_display ? "₹" + line.line_total_display : "") + "</div>" +
        "</div>" +
        '<div class="cart-line__row">' +
        '<button type="button" class="cart-line__remove" data-cart-remove="' + line.cart_id + '">Remove</button>' +
        "</div>" +
        "</div></div>"
      );
    },

    async add(productId, size, button) {
      if (!size) {
        toast("Pick a size", "Choose a size before adding this tee to your cart.", "error");
        const group = button ? button.closest("[data-size-group]") : null;
        if (group) {
          const error = group.querySelector(".size-error");
          if (error) error.textContent = "Please choose a size.";
          group.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        return;
      }
      if (button) {
        button.classList.add("is-loading");
        button.disabled = true;
      }
      try {
        const payload = await api("/api/cart/add", {
          method: "POST",
          body: { product_id: productId, size: size, quantity: 1 },
        });
        this.render(payload);
        this.saveLocal(payload);
        toast("Added to cart", payload.message || "");
        this.open();
      } catch (err) {
        toast("Could not add", err.message, "error");
      } finally {
        if (button) {
          button.classList.remove("is-loading");
          button.disabled = false;
        }
      }
    },

    async update(cartId, quantity) {
      try {
        const payload = await api("/api/cart/update", {
          method: "POST",
          body: { cart_id: cartId, quantity: quantity },
        });
        this.render(payload);
        this.saveLocal(payload);
      } catch (err) {
        toast("Could not update", err.message, "error");
        this.refresh();
      }
    },

    async remove(cartId) {
      try {
        const payload = await api("/api/cart/remove", {
          method: "POST",
          body: { cart_id: cartId },
        });
        this.render(payload);
        this.saveLocal(payload);
        toast("Removed", payload.message || "");
      } catch (err) {
        toast("Could not remove", err.message, "error");
      }
    },

    async clear() {
      try {
        const payload = await api("/api/cart/clear", { method: "POST", body: {} });
        this.render(payload);
        writeLocalCart([]);
      } catch (err) {
        toast("Could not clear the cart", err.message, "error");
      }
    },

    saveLocal(payload) {
      const items = (payload.lines || []).map((line) => ({
        product_id: line.product_id,
        size: line.size,
        quantity: line.quantity,
      }));
      writeLocalCart(items);
    },

    async sync() {
      const items = readLocalCart();
      if (!items.length) {
        await this.refresh();
        return;
      }
      try {
        const payload = await api("/api/cart/sync", { method: "POST", body: { items: items } });
        this.render(payload);
        this.saveLocal(payload);
      } catch (err) {
        this.refresh();
      }
    },
  };

  /* ------------------------------------------------------- add to cart UI */
  function initAddToCart() {
    document.addEventListener("click", (event) => {
      const sizeButton = event.target.closest(".size-pill");
      if (sizeButton && sizeButton.dataset.size) {
        if (sizeButton.disabled) return;
        const group = sizeButton.closest("[data-size-group]");
        if (group) {
          $$(".size-pill", group).forEach((pill) => pill.classList.remove("is-selected"));
          sizeButton.classList.add("is-selected");
          const error = group.querySelector(".size-error");
          if (error) error.textContent = "";
          const hidden = group.querySelector('input[type="hidden"][name="size"]');
          if (hidden) hidden.value = sizeButton.dataset.size;
          const info = group.closest(".pdp__info");
          if (info && sizeButton.dataset.priceDisplay) {
            const priceLabel = info.querySelector("[data-price-label]");
            if (priceLabel) priceLabel.textContent = "\u20b9" + sizeButton.dataset.priceDisplay;
            const addPrice = info.querySelector("[data-add-price]");
            if (addPrice) addPrice.textContent = sizeButton.dataset.priceDisplay;
          }
        }
        return;
      }

      const addButton = event.target.closest("[data-add-to-cart]");
      if (!addButton) return;
      event.preventDefault();
      const group = addButton.closest("[data-size-group]") || document;
      const selected = group.querySelector(".size-pill.is-selected");
      Cart.add(addButton.dataset.productId, selected ? selected.dataset.size : "", addButton);
    });

    // Quantity buttons inside the drawer.
    document.addEventListener("click", (event) => {
      const inc = event.target.closest("[data-cart-inc]");
      const dec = event.target.closest("[data-cart-dec]");
      const remove = event.target.closest("[data-cart-remove]");
      const clear = event.target.closest("[data-cart-clear]");
      if (inc) {
        const id = inc.dataset.cartInc;
        const line = (Cart.lastPayload && Cart.lastPayload.lines || []).find((l) => l.cart_id == id);
        if (line) Cart.update(id, line.quantity + 1);
      }
      if (dec) {
        const id = dec.dataset.cartDec;
        const line = (Cart.lastPayload && Cart.lastPayload.lines || []).find((l) => l.cart_id == id);
        if (line) Cart.update(id, line.quantity - 1);
      }
      if (remove) Cart.remove(remove.dataset.cartRemove);
      if (clear) Cart.clear();
    });
  }

  /* ------------------------------------------------------- product gallery */
  function initGallery() {
    const main = $("[data-gallery-main]");
    const thumbs = $$("[data-gallery-thumb]");
    if (!main || !thumbs.length) return;
    thumbs.forEach((thumb) => {
      thumb.addEventListener("click", () => {
        const source = thumb.dataset.src || thumb.querySelector("img").src;
        main.src = source;
        thumbs.forEach((other) => other.classList.remove("is-active"));
        thumb.classList.add("is-active");
      });
    });
  }

  /* ------------------------------------------------------- checkout helpers */
  function initCheckoutForm() {
    const form = $("[data-checkout-form]");
    if (!form) return;
    form.addEventListener("submit", () => {
      const button = form.querySelector('button[type="submit"]');
      if (button) {
        button.classList.add("is-loading");
        button.disabled = true;
        window.setTimeout(() => {
          button.classList.remove("is-loading");
          button.disabled = false;
        }, 12000);
      }
    });

    $$("[data-phone-input]").forEach((input) => {
      input.addEventListener("input", () => {
        const digits = input.value.replace(/\D/g, "").slice(0, 10);
        input.value = digits;
      });
    });

    const pinInput = $("[data-pincode-input]");
    const pinStatus = $("[data-pincode-status]");
    if (pinInput && pinStatus) {
      const checkPin = async () => {
        const pin = pinInput.value.replace(/\D/g, "");
        if (pin.length !== 6) {
          pinStatus.textContent = "";
          return;
        }
        try {
          const data = await api("/checkout/api/pincode/" + pin);
          pinStatus.textContent = data.message || "";
          pinStatus.style.color = data.ok ? "#157347" : "#b02a37";
        } catch (err) {
          pinStatus.textContent = err.message;
          pinStatus.style.color = "#b02a37";
        }
      };
      pinInput.addEventListener("blur", checkPin);
      if (pinInput.value.replace(/\D/g, "").length === 6) checkPin();
    }

    const otpButton = $("[data-otp-send]");
    const otpStatus = $("[data-otp-status]");
    if (otpButton && otpStatus) {
      otpButton.addEventListener("click", async () => {
        const phone = $("[data-phone-input]");
        const otpInput = $("#otp_code");
        otpStatus.textContent = "Sending...";
        try {
          const data = await api(otpButton.dataset.url, {
            method: "POST",
            body: { phone: phone ? phone.value : "" },
          });
          otpStatus.textContent = data.message || "Code sent.";
          otpStatus.style.color = "#157347";
          if (data.dev_code && otpInput) {
            otpInput.value = data.dev_code;
            otpStatus.textContent += " (test code filled in)";
          }
        } catch (err) {
          otpStatus.textContent = err.message;
          otpStatus.style.color = "#b02a37";
        }
      });
    }
  }

  /* --------------------------------------------------------------- wishlist */
  function initWishlist() {
    $$("[data-wishlist-toggle]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const data = await api("/api/wishlist/toggle", {
            method: "POST",
            body: { product_id: button.dataset.productId },
          });
          button.classList.toggle("is-active", data.added);
          button.textContent = data.added ? "♥ Saved" : "♡ Save";
          toast(data.message || "");
        } catch (err) {
          toast("Wishlist", err.message, "error");
        }
      });
    });
  }

  /* ------------------------------------------------------------ admin bits */
  function initAdmin() {
    const fileInput = $("[data-image-input]");
    const preview = $("[data-image-preview]");
    if (fileInput && preview) {
      fileInput.addEventListener("change", () => {
        preview.innerHTML = "";
        Array.from(fileInput.files || []).forEach((file) => {
          if (!file.type.startsWith("image/")) return;
          const img = document.createElement("img");
          img.src = URL.createObjectURL(file);
          img.alt = file.name;
          img.style.width = "100%";
          img.style.borderRadius = "8px";
          preview.appendChild(img);
        });
      });
    }

    const dropzone = $("[data-dropzone]");
    if (dropzone && fileInput) {
      ["dragenter", "dragover"].forEach((name) =>
        dropzone.addEventListener(name, (e) => {
          e.preventDefault();
          dropzone.classList.add("is-over");
        })
      );
      ["dragleave", "drop"].forEach((name) =>
        dropzone.addEventListener(name, (e) => {
          e.preventDefault();
          dropzone.classList.remove("is-over");
        })
      );
      dropzone.addEventListener("drop", (event) => {
        if (!event.dataTransfer || !event.dataTransfer.files.length) return;
        fileInput.files = event.dataTransfer.files;
        fileInput.dispatchEvent(new Event("change"));
      });
    }

    $$("[data-confirm]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (!window.confirm(button.dataset.confirm)) event.preventDefault();
      });
    });

    const statusSelect = $("[data-status-select]");
    if (statusSelect) {
      statusSelect.addEventListener("change", () => {
        const message = $("[data-status-message]");
        if (message) message.placeholder = "Note for " + statusSelect.value.replace(/_/g, " ") + "...";
      });
    }
  }

  /* ------------------------------------------------------------------ boot */
  function boot() {
    initNav();
    initStickyHeader();
    initFlashDismiss();
    Cart.init();
    initAddToCart();
    initGallery();
    initCheckoutForm();
    initWishlist();
    initAdmin();
    if (document.body.hasAttribute("data-clear-local-cart")) {
      writeLocalCart([]);
    }
    Cart.sync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.PT = { cart: Cart, api: api, toast: toast, rupees: rupees };
})();
