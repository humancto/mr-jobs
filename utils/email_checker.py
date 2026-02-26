"""
Email checker — Monitors inbox for application status updates.
Connects via IMAP to detect rejections, interview invites, and offers.
Supports Gmail (with app password), Outlook, and generic IMAP.
"""

import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from typing import Optional


# Known ATS and recruiter email domains
ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "workday.com", "icims.com",
    "myworkdayjobs.com", "jobvite.com", "smartrecruiters.com",
    "ashbyhq.com", "jazz.co", "bamboohr.com", "applytojob.com",
    "no-reply", "noreply", "careers@", "recruiting@", "talent@",
    "jobs@", "hiring@", "hr@"
]


def check_emails(profile: dict) -> list:
    """
    Check email for application-related messages.
    Returns list of detected status updates.
    """
    email_config = profile.get("email", {})
    if not email_config.get("enabled", False):
        return []

    imap_server = email_config.get("imap_server", "imap.gmail.com")
    email_addr = email_config.get("email", "")
    password = email_config.get("app_password", "")

    if not email_addr or not password:
        print("  ⚠ Email not configured (missing email or app_password)")
        return []

    results = []
    try:
        # Connect to IMAP
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, password)
        mail.select("INBOX")

        # Search for recent emails from ATS-like senders (last 7 days)
        since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")

        for domain in ATS_DOMAINS:
            try:
                _, messages = mail.search(None, f'(FROM "{domain}" SINCE "{since_date}")')
                if not messages[0]:
                    continue

                for msg_id in messages[0].split()[:20]:  # Limit per domain
                    try:
                        _, msg_data = mail.fetch(msg_id, "(RFC822)")
                        msg = email.message_from_bytes(msg_data[0][1])

                        subject = _decode_subject(msg.get("Subject", ""))
                        sender = msg.get("From", "")
                        date_str = msg.get("Date", "")

                        # Get body text
                        body = _get_body(msg)

                        # Classify the email
                        classification = _classify_email(subject, body)
                        if classification:
                            # Try to extract company name
                            company = _extract_company(sender, subject, body)
                            results.append({
                                "subject": subject,
                                "sender": sender,
                                "date": date_str,
                                "classification": classification,
                                "company": company,
                                "snippet": body[:200] if body else ""
                            })
                    except Exception:
                        continue
            except Exception:
                continue

        mail.close()
        mail.logout()

    except imaplib.IMAP4.error as e:
        print(f"  ⚠ IMAP login failed: {e}")
    except Exception as e:
        print(f"  ⚠ Email check failed: {e}")

    # Try to match results to tracked jobs and update statuses
    if results:
        _update_tracked_jobs(results)

    return results


def _decode_subject(subject: str) -> str:
    """Decode email subject line."""
    if not subject:
        return ""
    decoded = decode_header(subject)
    parts = []
    for content, charset in decoded:
        if isinstance(content, bytes):
            parts.append(content.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(content)
    return " ".join(parts)


def _get_body(msg) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _classify_email(subject: str, body: str) -> Optional[str]:
    """Classify an email as application-related status update."""
    text = f"{subject} {body}".lower()

    # Rejection patterns
    rejection = [
        "unfortunately", "not moving forward", "other candidates",
        "decided not to", "not selected", "position has been filled",
        "pursue other", "not the right fit", "regret to inform",
        "will not be moving", "won't be moving"
    ]
    if any(p in text for p in rejection):
        return "rejected"

    # Interview patterns
    interview = [
        "schedule an interview", "interview invitation", "like to invite you",
        "next step", "phone screen", "technical interview", "on-site",
        "video call", "meet the team", "would love to chat",
        "calendar invite", "book a time"
    ]
    if any(p in text for p in interview):
        return "interviewing"

    # Offer patterns
    offer = [
        "offer letter", "pleased to offer", "job offer",
        "compensation package", "start date", "formal offer",
        "excited to extend"
    ]
    if any(p in text for p in offer):
        return "offer"

    # Acknowledgement patterns
    ack = [
        "application received", "thank you for applying",
        "we received your application", "application has been submitted",
        "confirming your application"
    ]
    if any(p in text for p in ack):
        return "acknowledged"

    return None


def _extract_company(sender: str, subject: str, body: str) -> str:
    """Try to extract company name from email."""
    # Try from sender display name
    if "<" in sender:
        name = sender.split("<")[0].strip().strip('"')
        if name and len(name) > 2:
            # Remove common suffixes
            for suffix in ["Recruiting", "Careers", "Talent", "HR", "Jobs", "Hiring"]:
                name = name.replace(suffix, "").strip()
            if name:
                return name

    # Try from subject
    for prefix in ["at ", "from ", "with "]:
        if prefix in subject.lower():
            idx = subject.lower().index(prefix) + len(prefix)
            company = subject[idx:].split()[0:2]
            return " ".join(company).strip(".,!;:")

    return "Unknown"


def _update_tracked_jobs(results: list):
    """Try to match email results to tracked jobs and update statuses."""
    from utils.tracker import get_all_jobs, update_job_status

    all_jobs, _ = get_all_jobs(limit=1000)

    for result in results:
        company = result["company"].lower()
        classification = result["classification"]

        if classification in ("rejected", "interviewing", "offer"):
            for job in all_jobs:
                if company in job.get("company", "").lower():
                    # Only update if the new status makes sense
                    current = job.get("status", "")
                    if current in ("applied", "matched", "discovered"):
                        update_job_status(job["id"], classification)
                        print(f"  📧 Email update: {job['title']} @ {job['company']} -> {classification}")
                    break
