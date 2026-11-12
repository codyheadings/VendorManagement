def fmt_price(amount, currency, rate_to_usd):
    """Return (main_line, caption). Converted to USD when we have a rate."""
    if amount is None:
        return "—", "Total not stated"
    if currency == "USD":
        return f"${amount:,.2f}", ""
    if rate_to_usd:
        return f"${amount * rate_to_usd:,.2f}", f"converted from {amount:,.2f} {currency}"
    return f"{amount:,.2f} {currency}", "no exchange rate on file"