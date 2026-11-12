"""Slack alerts: new items to review, vendors who are late replying, legal documents due for renewal.

    python notify.py            # look for anything new and send it (prints what it would send if no webhook)
    python notify.py --dry-run  # show what would be sent, send nothing, remember nothing

Design
  * Each alert has a stable key and is sent ONCE (kept in notification_log). A failed send is not
    recorded, so the next run retries it. Messages are grouped: at most one per kind per run.
  * Messages carry names and dates only: no prices, no email addresses, no document text.
  * Slack trouble never breaks the app: every function returns a report instead of raising.
  * 'Late' means the RFQ due date has passed and the vendor was invited but has not replied. It
    repeats weekly until they reply. Legal alerts fire once per stage (60 days out, notice deadline, expired).
"""
import argparse
import json
import os
import sys
import urllib.error
import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
import db
import extraction.loader as loader

MAX_LINES = 10                    # lines per message; the rest become "and N more"
RENEWAL_WARNING_DAYS = 60
NOTICE_WARNING_DAYS = 14
LATE_REPEAT_DAYS = 7
MIN_MINUTES_BETWEEN_RUNS = 60
_TIMEOUT = 10

_DDL = """CREATE TABLE IF NOT EXISTS notification_log (
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  sent_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT"""


@dataclass
class Alert:
    key: str
    kind: str                      # review | late | legal
    line: str


@dataclass
class Report:
    configured: bool
    sent: int = 0                  # alerts delivered
    messages: int = 0
    pending: list[str] = field(default_factory=list)      # lines not sent (dry run / not configured)
    errors: list[str] = field(default_factory=list)


# ---------- configuration and transport ----------

def _secrets_files() -> list[Path]:
    """Where Streamlit looks for secrets, plus the project folder (so the command line works from anywhere)."""
    return [db.BASE_DIR / ".streamlit" / "secrets.toml", Path.cwd() / ".streamlit" / "secrets.toml",
            Path.home() / ".streamlit" / "secrets.toml"]


def _secret(name: str) -> tuple[str | None, str]:
    """Look a setting up. Returns (value or None, where it came from or why it was not found)."""
    if os.environ.get(name):
        return os.environ[name].strip(), "environment variable"
    for path in dict.fromkeys(_secrets_files()):          # the same file may be listed twice
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            return None, f"{path} could not be read ({type(e).__name__}: {e})"
        if isinstance(data.get(name), str) and data[name].strip():
            return data[name].strip(), str(path)
        return None, (f"{path} has no top-level {name}. Top-level keys found: "
                      f"{', '.join(k for k, v in data.items() if not isinstance(v, dict)) or '(none)'}"
                      + (f"; sections: {', '.join(k for k, v in data.items() if isinstance(v, dict))}" if any(isinstance(v, dict) for v in data.values()) else ""))
    return None, "no secrets.toml found in: " + "; ".join(str(p) for p in dict.fromkeys(_secrets_files()))


def webhook_url() -> str | None:
    return _secret("SLACK_WEBHOOK_URL")[0]


def app_url() -> str | None:
    return _secret("APP_URL")[0]


def diagnose() -> list[str]:
    """Plain-language report on why alerts may not be reaching Slack. Never prints the URL itself."""
    url, where = _secret("SLACK_WEBHOOK_URL")
    if url is None:
        return [f"SLACK_WEBHOOK_URL not found: {where}"]
    out = [f"SLACK_WEBHOOK_URL found ({where})"]
    if not url.startswith("https://hooks.slack.com/"):
        out.append("It does not start with https://hooks.slack.com/ : check you copied the Incoming Webhook URL, "
                   "not an app or channel link.")
    if url != url.strip('"\' '):
        out.append("It contains stray quote characters.")
    return out


def post_to_slack(url: str, text: str) -> tuple[bool, str]:
    """Send one message. Returns (ok, error text). The URL is a secret and never appears in errors."""
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return (True, "") if resp.status == 200 else (False, f"Slack answered {resp.status}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                continue                                    # one retry for rate limits / server trouble
            return False, f"Slack answered {e.code}"
        except Exception as e:  # noqa: BLE001  network down, DNS, timeout
            if attempt == 1:
                continue
            return False, f"could not reach Slack ({type(e).__name__})"
    return False, "could not reach Slack"


# ---------- finding things to say ----------

def _ensure() -> None:
    with db.transaction() as conn:
        conn.execute(_DDL)


def _already(keys: list[str]) -> set[str]:
    if not keys:
        return set()
    marks = ",".join("?" * len(keys))
    return {r["key"] for r in db.query(f"SELECT key FROM notification_log WHERE key IN ({marks})", keys)}


def review_alerts() -> list[Alert]:
    out = []
    for r in db.query("""SELECT q.id, v.name AS v, q.reference_number AS ref FROM quotes q
                         JOIN vendors v ON v.id = q.vendor_id WHERE q.review_status = 'pending'"""):
        out.append(Alert(f"review:quote:{r['id']}", "review", f"Quote from {r['v']}" + (f" ({r['ref']})" if r["ref"] else "")))
    for r in db.query("""SELECT l.id, l.doc_type, v.name AS v FROM legal_documents l
                         JOIN vendors v ON v.id = l.vendor_id WHERE l.review_status = 'pending'"""):
        out.append(Alert(f"review:legal:{r['id']}", "review", f"{r['doc_type']} with {r['v']}"))
    for r in db.query("""SELECT i.id, i.invoice_number AS num, v.name AS v FROM invoices i
                         JOIN vendors v ON v.id = i.vendor_id WHERE i.review_status = 'pending'"""):
        out.append(Alert(f"review:invoice:{r['id']}", "review", f"Invoice {r['num']} from {r['v']}"))
    for r in loader.review_queue():
        what = {"needs_vendor": "needs a vendor", "invalid": "failed checks", "failed": "failed to load"}.get(r["status"], "needs follow-up")
        out.append(Alert(f"review:extraction:{r['id']}:{r['status']}", "review", f"{r['file_name']}: {what}"))
    return out


def late_alerts() -> list[Alert]:
    out = []
    for r in db.query(
            """SELECT i.rfq_id, i.vendor_id, r.rfq_number, r.due_date, v.name AS v FROM rfq_invitations i
               JOIN rfqs r ON r.id = i.rfq_id JOIN vendors v ON v.id = i.vendor_id
               WHERE i.status = 'sent' AND r.status = 'open' AND r.due_date IS NOT NULL AND r.due_date < ?""", (db.TODAY,)):
        days = (date.fromisoformat(db.TODAY) - date.fromisoformat(r["due_date"])).days
        out.append(Alert(f"late:{r['rfq_id']}:{r['vendor_id']}:w{days // LATE_REPEAT_DAYS}", "late",
                         f"{r['v']} has not replied to {r['rfq_number']} ({days} day{'s' * (days != 1)} past the {r['due_date']} deadline)"))
    return out


def legal_alerts() -> list[Alert]:
    out = []
    today = date.fromisoformat(db.TODAY)
    for r in db.query(
            """SELECT l.id, l.doc_type, l.expiry_date, l.auto_renews, l.notice_days, v.name AS v FROM legal_documents l
               JOIN vendors v ON v.id = l.vendor_id
               WHERE l.review_status = 'approved' AND l.status IN ('signed') AND l.expiry_date IS NOT NULL"""):
        expiry = date.fromisoformat(r["expiry_date"])
        label = f"{r['doc_type']} with {r['v']}"
        renew = " It auto-renews unless cancelled." if r["auto_renews"] else ""
        if expiry < today:
            out.append(Alert(f"legal:{r['id']}:expired", "legal", f"{label} expired on {r['expiry_date']}."))
            continue
        if r["notice_days"]:
            deadline = expiry - timedelta(days=r["notice_days"])
            if today >= deadline - timedelta(days=NOTICE_WARNING_DAYS):
                when = "was" if deadline < today else "is"
                out.append(Alert(f"legal:{r['id']}:notice", "legal",
                                 f"{label}: the {r['notice_days']}-day notice deadline {when} {deadline.isoformat()} "
                                 f"(expires {r['expiry_date']}).{renew}"))
                continue
        if (expiry - today).days <= RENEWAL_WARNING_DAYS:
            out.append(Alert(f"legal:{r['id']}:soon", "legal", f"{label} expires on {r['expiry_date']}.{renew}"))
    return out


def payment_alerts() -> list[Alert]:
    """Approved, unpaid invoices past their due date. Repeats weekly until a payment is recorded."""
    out = []
    for r in db.query(
            """SELECT i.id, i.invoice_number AS num, i.due_date, v.name AS v FROM invoices i JOIN vendors v ON v.id = i.vendor_id
               WHERE i.review_status = 'approved' AND i.paid_date IS NULL AND i.due_date IS NOT NULL AND i.due_date < ?""", (db.TODAY,)):
        days = (date.fromisoformat(db.TODAY) - date.fromisoformat(r["due_date"])).days
        out.append(Alert(f"pay:{r['id']}:w{days // LATE_REPEAT_DAYS}", "pay",
                         f"Invoice {r['num']} from {r['v']} is {days} day{'s' * (days != 1)} overdue (due {r['due_date']})"))
    return out


_HEADERS = {"review": ":inbox_tray: *{n} item{s} waiting for review*",
            "late": ":alarm_clock: *{n} late vendor repl{s}*",
            "legal": ":page_facing_up: *{n} legal document{s} need attention*",
            "pay": ":money_with_wings: *{n} overdue invoice{s}*"}


def _message(kind: str, alerts: list[Alert]) -> str:
    n = len(alerts)
    plural = {"s": "" if n == 1 else "s"}
    head = _HEADERS[kind].format(n=n, s=("y" if n == 1 else "ies") if kind == "late" else plural["s"])
    lines = [f"• {a.line}" for a in alerts[:MAX_LINES]]
    if n > MAX_LINES:
        lines.append(f"…and {n - MAX_LINES} more")
    link = app_url()
    if link:
        lines.append(f"<{link}|Open the app>")
    return head + "\n" + "\n".join(lines)


# ---------- running ----------

def run_checks(kinds: tuple[str, ...] = ("review", "late", "legal", "pay"), dry_run: bool = False, sender=None) -> Report:
    """Find unsent alerts and send them. sender(url, text) -> (ok, error) can be replaced in tests."""
    # Global notification toggle
    if not db.NOTIFICATIONS_ENABLED:
        return Report(
            configured=False,
            pending=[],
            errors=[]
        )

    _ensure()
    url = webhook_url()
    report = Report(configured=bool(url))
    sender = sender or post_to_slack
    finders = {"review": review_alerts, "late": late_alerts, "legal": legal_alerts}
    for kind in kinds:
        try:
            alerts = finders[kind]()
        except Exception as e:
            report.errors.append(f"{kind}: could not check ({type(e).__name__}: {e})")
            continue
        seen = _already([a.key for a in alerts])
        fresh = [a for a in alerts if a.key not in seen]
        if not fresh:
            continue
        text = _message(kind, fresh)
        if dry_run or not url:
            report.pending.append(text)
            continue
        ok, err = sender(url, text)
        if not ok:
            report.errors.append(f"{kind}: {err}")       # not logged, so the next run retries
            continue
        with db.transaction() as conn:
            conn.executemany("INSERT OR IGNORE INTO notification_log (key, kind) VALUES (?, ?)", [(a.key, kind) for a in fresh])
        report.sent += len(fresh)
        report.messages += 1
    return report


def run_if_due(sender=None) -> Report | None:
    """For app start-up: run at most once per MIN_MINUTES_BETWEEN_RUNS."""

    if not db.NOTIFICATIONS_ENABLED:
        return None
    
    _ensure()
    row = db.query_one("SELECT sent_at FROM notification_log WHERE key = 'last_run'")
    if row and db.scalar("SELECT (julianday('now') - julianday(?)) * 1440", (row["sent_at"],)) < MIN_MINUTES_BETWEEN_RUNS:
        return None
    report = run_checks(sender=sender)

    if not db.NOTIFICATIONS_ENABLED:
        return None

    with db.transaction() as conn:
        conn.execute("INSERT INTO notification_log (key, kind) VALUES ('last_run', 'meta') "
                     "ON CONFLICT(key) DO UPDATE SET sent_at = datetime('now')")
    return report


def main(argv: list[str] | None = None) -> int:
    if not db.NOTIFICATIONS_ENABLED:
        print("Slack notifications are disabled.")
        return 0
    
    ap = argparse.ArgumentParser(description="Send Slack alerts.")
    ap.add_argument("--dry-run", action="store_true", help="show what would be sent; send and remember nothing")
    ap.add_argument("--check-config", action="store_true", help="explain whether the Slack webhook is found")
    ap.add_argument("--test", action="store_true", help="send one test message to the channel")
    args = ap.parse_args(argv)
    if args.check_config or args.test:
        for line in diagnose():
            print(line)
        url = webhook_url()
        if args.test and url:
            ok, err = post_to_slack(url, ":white_check_mark: Test message from the vendor platform.")
            print("Test message sent. Check the channel." if ok else f"Test message failed: {err}")
            return 0 if ok else 1
        return 0 if url else 1
    db.ensure_db()
    r = run_checks(dry_run=args.dry_run)
    if not r.configured and not args.dry_run:
        print("SLACK_WEBHOOK_URL is not set, so nothing was sent. Would have sent:\n")
    for m in r.pending:
        print(m, "\n")
    for e in r.errors:
        print("Problem:", e, file=sys.stderr)
    if r.configured and not args.dry_run:
        print(f"Sent {r.sent} alert(s) in {r.messages} message(s).")
    elif not r.pending and not r.errors:
        print("Nothing new to report.")
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main())