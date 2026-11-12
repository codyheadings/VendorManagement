import smtplib
import streamlit as st
from email.message import EmailMessage

def build_rfq_email(vendor, rfq_data, pdf_bytes):
    msg = EmailMessage()

    msg["Subject"] = (
        f"Request for Quote - {rfq_data['rfq_subtitle']}"
    )

    msg["To"] = vendor["email"]

    msg["Body"] = None

    msg.set_content(f"""
Hello {vendor['contact_name']},

We are requesting a quotation for the following:

Purpose: {rfq_data['rfq_subtitle']}

Quote Deadline: {rfq_data['rfq_deadline']}

Deliver To: {rfq_data['deliver_to']}

Please review the attached RFQ document and submit
your quotation by the deadline indicated.

Thank you,

{rfq_data['rfq_contact']}
""")

    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=f"{rfq_data['rfq_id']}.pdf"
    )

    return msg

def send_email(msg):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:

        smtp.login(
            st.secrets["email"]["MAIL_SENDER"],
            st.secrets["email"]["GOOGLE_APP_PASSWORD"]
        )

        smtp.send_message(msg)

def build_simple_msg(contact_email: str, subject: str, body: str):
    msg = EmailMessage()

    msg["Subject"] = f"{subject.rstrip()}"
    msg["To"] = f"{contact_email.rstrip()}"
    msg.set_content(body.rstrip())
    
    return msg