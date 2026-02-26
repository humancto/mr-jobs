"""
Generic Form Adapter — AI-driven form filling for any job application page.

Uses Claude Code CLI to analyze arbitrary HTML forms and generate
fill instructions. This is the fallback for sites without a dedicated adapter.
"""

import random
from playwright.async_api import Page
from utils.brain import ClaudeBrain
from utils.answers import find_cached_answer, get_personal_field


async def apply_generic(
    page: Page,
    job_url: str,
    profile: dict,
    brain: ClaudeBrain,
    cover_letter: str = "",
    dry_run: bool = True,
    max_wizard_steps: int = 8
):
    """
    AI-driven form filler for arbitrary job application pages.
    Handles both single-page forms and multi-step wizards.
    """
    personal = profile["personal"]

    print(f"  📝 Loading application page...")
    await page.goto(job_url, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    step = 0
    while step < max_wizard_steps:
        step += 1
        print(f"\n  --- Step {step} ---")

        # Grab current visible form HTML
        form_html = await page.evaluate("""() => {
            // Try to find the most relevant form container
            const selectors = [
                '[role="dialog"]',
                'form[class*="application"]',
                'form[class*="apply"]',
                'form',
                'main',
                '[class*="application"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerHTML.length > 100) {
                    return el.outerHTML;
                }
            }
            return document.body.innerHTML;
        }""")

        # Truncate for CLI context limits
        form_html = form_html[:14000]

        # Ask Claude to analyze the form
        try:
            instructions = brain.ask_json(f"""You are automating a job application form.

APPLICANT:
{_format_profile(profile)}

COVER LETTER (use if there's a cover letter field):
{cover_letter[:1000] if cover_letter else 'N/A'}

CURRENT PAGE HTML:
{form_html}

Analyze the form and return a JSON object:
{{
  "status": "fill_and_next" | "submit" | "done" | "no_form",
  "description": "<what you see on this page>",
  "fields": [
    {{"action": "fill", "selector": "<CSS>", "value": "<text>", "note": "<field name>"}},
    {{"action": "select", "selector": "<CSS>", "value": "<option>", "note": "<field name>"}},
    {{"action": "check", "selector": "<CSS>", "note": "<checkbox name>"}},
    {{"action": "upload", "selector": "<CSS>", "file_key": "resume", "note": "resume upload"}}
  ],
  "next_button": "<CSS selector for next/submit/continue button>"
}}

Rules:
- status "done" = form already submitted / confirmation page
- status "no_form" = no application form found
- Use robust CSS selectors (prefer #id, [name=...], [aria-label=...])
- For file uploads, set file_key to "resume"
- Put the most standard fields first (name, email, phone)
""")
        except Exception as e:
            print(f"  ⚠ Claude analysis failed: {e}")
            break

        status = instructions.get("status", "no_form")
        desc = instructions.get("description", "")
        print(f"  📋 {desc}")

        if status == "done":
            print(f"  ✅ Application appears to be submitted!")
            return True

        if status == "no_form":
            print(f"  ⚠ No application form found on this page")
            return False

        # Execute field fills
        fields = instructions.get("fields", [])
        for field_inst in fields:
            action = field_inst.get("action", "")
            selector = field_inst.get("selector", "")
            value = field_inst.get("value", "")
            note = field_inst.get("note", "")

            try:
                el = await page.wait_for_selector(selector, timeout=3000)
                if not el:
                    continue

                if action == "fill":
                    await el.fill(value)
                    print(f"    ✅ {note}: filled")
                elif action == "select":
                    try:
                        await el.select_option(value=value)
                    except Exception:
                        await el.select_option(label=value)
                    print(f"    ✅ {note}: selected '{value}'")
                elif action == "check":
                    is_checked = await el.is_checked()
                    if not is_checked:
                        await el.check()
                    print(f"    ✅ {note}: checked")
                elif action == "upload":
                    await el.set_input_files(profile["resume_path"])
                    print(f"    ✅ {note}: uploaded")

                await page.wait_for_timeout(random.randint(300, 700))

            except Exception as e:
                print(f"    ⚠ {note or selector}: {e}")

        # Click next/submit
        next_btn_selector = instructions.get("next_button")
        if next_btn_selector:
            if status == "submit" and dry_run:
                print(f"\n  🏁 DRY RUN — Would click submit: {next_btn_selector}")
                print(f"     Review the form in the browser window")
                return True

            try:
                btn = await page.wait_for_selector(next_btn_selector, timeout=5000)
                if btn:
                    if status == "submit":
                        print(f"  🚀 Submitting...")
                    else:
                        print(f"  ➡️  Next step...")
                    await btn.click()
                    await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  ⚠ Button click failed ({next_btn_selector}): {e}")
                break

        if status == "submit":
            # Check for confirmation
            await page.wait_for_timeout(2000)
            body_text = await page.inner_text("body")
            if any(w in body_text.lower() for w in ["thank you", "submitted", "confirmation", "received"]):
                print(f"  ✅ Application submitted successfully!")
                return True
            else:
                print(f"  ⚠ Submitted but no clear confirmation")
                return True

    print(f"  ⚠ Reached max wizard steps ({max_wizard_steps})")
    return False


def _format_profile(profile: dict) -> str:
    """Format profile for the AI prompt."""
    p = profile["personal"]
    common = profile.get("common_answers", {})
    lines = [
        f"Name: {p.get('first_name', '')} {p.get('last_name', '')}",
        f"Email: {p.get('email', '')}",
        f"Phone: {p.get('phone', '')}",
        f"Location: {p.get('location', '')}",
        f"LinkedIn: {p.get('linkedin', '')}",
        f"GitHub: {p.get('github', '')}",
        f"Website: {p.get('portfolio', '')}",
    ]
    for key, val in common.items():
        lines.append(f"{key}: {val}")
    return "\n".join(lines)
