import streamlit as st

import services.purchasing_service as ps


def _usd(v: float | None) -> str:
    return f"${v:,.0f}" if v is not None else "-"


def _money(amount: float | None, currency: str | None) -> str:
    return f"{currency or ''} {amount:,.2f}".strip() if amount is not None else "-"


def header_metrics(vendor: dict) -> dict:
    """Show the key facts in one row and return the money summary so the page can reuse it."""
    summary = ps.vendor_summary(vendor["id"])
    cols = st.columns(5)
    cols[0].metric("Category", vendor.get("category") or "Not specified")
    cols[1].metric("Country", vendor.get("country") or "Not specified")
    cols[2].metric("Paid to date", _usd(summary["paid"]), help="Approved invoices marked as paid, in USD.")
    cols[3].metric("Outstanding", _usd(summary["outstanding"]),
                   delta=f"{_usd(summary['overdue'])} overdue" if summary["overdue"] else None,
                   delta_color="inverse", help="Approved invoices not yet paid, in USD.")
    cols[4].metric("Open on POs", _usd(summary["committed"]),
                   help="What open purchase orders have not yet been invoiced for, in USD.")
    return summary


def _status_label(status: str) -> str:
    return {"paid": "Paid", "unpaid": "Unpaid", "overdue": "Overdue", "pending": "Awaiting review"}.get(status, status.title())


def section(summary: dict, tab_po, tab_inv, tab_spent) -> None:
    pos, invoices = summary["pos"], summary["invoices"]

    if summary["unconverted"]:
        st.warning(
            f"{summary['unconverted']} amount(s) are in a currency with no stored exchange rate "
            "and are left out of the USD totals."
        )

    with tab_po:
        st.subheader("Purchase Orders")

        if not pos:
            st.info("No purchase orders for this vendor yet.")
        else:
            st.dataframe(
                [
                    {
                        "PO": p["po_number"],
                        "Issued": p["issued_date"],
                        "Status": p["status"].title(),
                        "Amount": _money(p["amount"], p["currency"]),
                        "Invoiced": _money(p["invoiced"], p["currency"]),
                        "Remaining": _money(max(p["remaining"], 0), p["currency"]),
                    }
                    for p in pos
                ],
                hide_index=True,
                use_container_width=True,
            )

    with tab_inv:
        st.subheader("Invoice Information")
        if not invoices:
            st.info("No invoices for this vendor yet.")
        else:
            st.dataframe(
                [
                    {
                        "Invoice": i["invoice_number"],
                        "PO": i["po_number"] or "-",
                        "Invoice date": i["invoice_date"],
                        "Due": i["due_date"] or "-",
                        "Paid on": i["paid_date"] or "-",
                        "Amount": _money(i["amount"], i["currency"]),
                        "USD": i["amount_usd"],
                        "Status": _status_label(i["status"]),
                    }
                    for i in invoices
                ],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "USD": st.column_config.NumberColumn("USD", format="$%,.0f")
                },
            )

            waiting = sum(1 for i in invoices if i["status"] == "pending")
            if waiting:
                st.caption(
                    f"{waiting} invoice(s) are waiting in the review queue "
                    "and are not counted in the totals yet."
                )

    with tab_spent:
        st.subheader("Amount Spent (USD)")
        if not summary["by_month"]:
            st.info("No approved invoices yet, so there is no payment history.")
        else:
            st.bar_chart(
                {
                    "Invoiced": {
                        m["month"]: m["invoiced"] for m in summary["by_month"]
                    },
                    "Paid": {
                        m["month"]: m["paid"] for m in summary["by_month"]
                    },
                }
            )
            st.caption(
                f"USD at the stored exchange rates. Invoiced by invoice date, "
                f"paid by payment date. Total invoiced {_usd(summary['invoiced'])}, "
                f"paid {_usd(summary['paid'])}."
            )