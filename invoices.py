"""GST invoice data for an order."""

from __future__ import annotations

from typing import Any, Dict

from flask import current_app


def invoice_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Split the GST that is already included in the prices."""
    percent = int(current_app.config.get("GST_PERCENT") or 5)
    total = int(order["total_cents"])
    tax_total = total * percent // (100 + percent) if percent else 0
    half = tax_total // 2
    return {
        "invoice_number": f"{current_app.config.get('INVOICE_PREFIX') or 'INV'}-{order['order_number']}",
        "legal_name": current_app.config.get("STORE_LEGAL_NAME")
        or current_app.config.get("STORE_NAME"),
        "gstin": current_app.config.get("STORE_GSTIN") or "",
        "gst_percent": percent,
        "taxable_cents": total - tax_total,
        "cgst_cents": half,
        "sgst_cents": tax_total - half,
        "tax_cents": tax_total,
        "total_cents": total,
    }
