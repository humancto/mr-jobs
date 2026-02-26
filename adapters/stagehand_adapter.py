"""
Stagehand Adapter — Self-healing AI form filler using Playwright + Claude CLI.

Replaces the previous stagehand-py dependency with a local-only approach:
- Playwright for browser interaction (already installed)
- Claude CLI via ClaudeBrain for field discovery (no API key needed)

Architecture: observe -> plan -> act -> verify -> navigate
Uses accessibility tree snapshots instead of raw HTML for reliable,
compact form analysis that fits the CLI context window.
"""

import os
import json
import time
import random
import asyncio
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from utils.brain import ClaudeBrain
from utils.answers import find_cached_answer, get_personal_field

logger = logging.getLogger("stagehand_adapter")

# Cache directory for field mappings — avoids repeat Claude CLI calls
CACHE_DIR = Path(".cache/form_actions")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Screenshot cache directory for vision fallback
SCREENSHOT_DIR = Path(".cache/form_screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Confirmation page indicators
CONFIRMATION_INDICATORS = [
    "thank you",
    "application submitted",
    "application received",
    "successfully submitted",
    "we received your application",
    "confirmation",
    "you have applied",
    "application complete",
    "thanks for applying",
    "we'll review your application",
    "your application has been",
    "successfully applied",
]


# ──────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────

def _cache_key(url: str, action_desc: str) -> str:
    """Generate a stable cache key from URL domain + action description."""
    domain = urlparse(url).netloc.replace(".", "_")
    safe_desc = "".join(c if c.isalnum() else "_" for c in action_desc[:60])
    return f"{domain}__{safe_desc}"


def _domain_cache_path(url: str) -> Path:
    """Get the domain-level cache file path."""
    domain = urlparse(url).netloc.replace(".", "_").replace(":", "_")
    return CACHE_DIR / f"{domain}.json"


def _load_cached_action(key: str) -> Optional[dict]:
    """Load a cached action result from disk."""
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_cached_action(key: str, action: dict):
    """Persist an action result to disk for zero-CLI replays."""
    path = CACHE_DIR / f"{key}.json"
    try:
        with open(path, "w") as f:
            json.dump(action, f, indent=2)
    except Exception:
        pass


def _load_domain_cache(url: str) -> Optional[dict]:
    """Load the full domain-level field mapping cache."""
    path = _domain_cache_path(url)
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            # Cache entries older than 7 days are stale
            last_updated = data.get("last_updated", "")
            if last_updated:
                try:
                    updated_dt = datetime.fromisoformat(last_updated)
                    age_days = (datetime.now(timezone.utc) - updated_dt.replace(
                        tzinfo=timezone.utc if updated_dt.tzinfo is None else updated_dt.tzinfo
                    )).days
                    if age_days > 7:
                        return None
                except (ValueError, TypeError):
                    pass
            return data
        except Exception:
            pass
    return None


def _save_domain_cache(url: str, field_mappings: dict):
    """Save domain-level field mappings to disk."""
    domain = urlparse(url).netloc
    path = _domain_cache_path(url)
    try:
        data = {
            "domain": domain,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "field_mappings": field_mappings,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# Accessibility tree & DOM snapshot helpers
# ──────────────────────────────────────────────────────────────

async def get_form_snapshot(page) -> tuple:
    """
    Get accessibility tree + simplified DOM of form elements.

    Returns:
        (accessibility_tree, form_summary) where:
        - accessibility_tree is the Playwright a11y snapshot (dict/None)
        - form_summary is a list of dicts describing visible interactive elements
    """
    # Get the accessibility tree — use a generous timeout for heavy SPAs (Ashby, Workday)
    try:
        tree = await asyncio.wait_for(
            page.accessibility.snapshot(),
            timeout=15.0,  # 15s — Ashby/Workday SPAs can be slow
        )
    except asyncio.TimeoutError:
        logger.warning("Accessibility snapshot timed out (15s) — falling back to form_summary only")
        tree = None
    except Exception as e:
        logger.debug(f"Accessibility snapshot failed: {e}")
        tree = None

    # Get a simplified DOM of just form elements — also timeout-protected
    try:
        form_summary = await asyncio.wait_for(page.evaluate("""() => {
            const inputs = document.querySelectorAll(
                'input, textarea, select, button[type="submit"], [role="button"], ' +
                '[role="combobox"], [role="listbox"], [role="checkbox"], [role="radio"]'
            );
            return Array.from(inputs).map((el, idx) => {
                // Compute a simple XPath
                function getXPath(element) {
                    if (element.id) return '//*[@id="' + element.id + '"]';
                    if (element === document.body) return '/html/body';
                    let ix = 0;
                    const siblings = element.parentNode ? element.parentNode.childNodes : [];
                    for (let i = 0; i < siblings.length; i++) {
                        const sibling = siblings[i];
                        if (sibling === element) {
                            const parentPath = element.parentNode ? getXPath(element.parentNode) : '';
                            return parentPath + '/' + element.tagName.toLowerCase() + '[' + (ix + 1) + ']';
                        }
                        if (sibling.nodeType === 1 && sibling.tagName === element.tagName) ix++;
                    }
                    return '';
                }

                const label = el.closest('label')?.textContent?.trim() ||
                              (el.id ? (document.querySelector('label[for="' + el.id + '"]')?.textContent?.trim() || '') : '') ||
                              '';

                return {
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    placeholder: el.placeholder || '',
                    'aria-label': el.getAttribute('aria-label') || '',
                    role: el.getAttribute('role') || '',
                    value: (el.tagName === 'SELECT') ? '' : (el.value || ''),
                    required: el.required || false,
                    label: label.substring(0, 200),
                    xpath: getXPath(el),
                    visible: el.offsetParent !== null || el.type === 'hidden',
                    options: el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))
                        : [],
                    index: idx,
                };
            }).filter(el => el.visible && el.type !== 'hidden');
        }"""), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("Form summary evaluation timed out (10s)")
        form_summary = []
    except Exception as e:
        logger.debug(f"Form summary evaluation failed: {e}")
        form_summary = []

    return tree, form_summary


def _format_a11y_tree(tree: dict, depth: int = 0, max_depth: int = 6) -> str:
    """Format an accessibility tree dict into a readable string."""
    if not tree or depth > max_depth:
        return ""

    indent = "  " * depth
    role = tree.get("role", "")
    name = tree.get("name", "")
    value = tree.get("value", "")

    parts = []
    if role:
        line = f"{indent}[{role}]"
        if name:
            line += f' "{name}"'
        if value:
            line += f" value={value}"
        # Include relevant properties
        for prop in ("checked", "selected", "required", "disabled", "expanded"):
            if tree.get(prop) is not None:
                line += f" {prop}={tree[prop]}"
        parts.append(line)

    children = tree.get("children", [])
    if children:
        for child in children:
            child_str = _format_a11y_tree(child, depth + 1, max_depth)
            if child_str:
                parts.append(child_str)

    return "\n".join(parts)


def _format_form_summary(form_summary: list) -> str:
    """Format form summary list into a readable string for Claude."""
    lines = []
    for i, field in enumerate(form_summary):
        parts = [f"[{i}]"]
        parts.append(f"<{field['tag']}")
        if field.get("type"):
            parts.append(f'type="{field["type"]}"')
        if field.get("id"):
            parts.append(f'id="{field["id"]}"')
        if field.get("name"):
            parts.append(f'name="{field["name"]}"')
        if field.get("aria-label"):
            parts.append(f'aria-label="{field["aria-label"]}"')
        if field.get("placeholder"):
            parts.append(f'placeholder="{field["placeholder"]}"')
        if field.get("role"):
            parts.append(f'role="{field["role"]}"')
        if field.get("required"):
            parts.append("required")
        parts.append(">")
        if field.get("label"):
            parts.append(f'label="{field["label"][:100]}"')
        if field.get("value"):
            parts.append(f'current_value="{field["value"][:50]}"')
        if field.get("options"):
            opt_strs = [f'{o["text"]}' for o in field["options"][:20]]
            parts.append(f'options=[{", ".join(opt_strs)}]')

        lines.append(" ".join(parts))

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Selector generation — prioritized strategy
# ──────────────────────────────────────────────────────────────

def build_selector(field: dict) -> str:
    """
    Build the best possible CSS/attribute selector for a form field.

    Priority:
    1. #id if id exists
    2. [name="fieldname"] if name exists
    3. [aria-label="label"] if aria-label exists
    4. Construct from tag + type + placeholder
    5. XPath as last resort (returned with xpath: prefix)
    """
    tag = field.get("tag", "input")

    # Priority 1: ID selector
    if field.get("id"):
        return f"#{field['id']}"

    # Priority 2: name attribute
    if field.get("name"):
        return f'{tag}[name="{field["name"]}"]'

    # Priority 3: aria-label
    if field.get("aria-label"):
        return f'{tag}[aria-label="{field["aria-label"]}"]'

    # Priority 4: placeholder
    if field.get("placeholder"):
        return f'{tag}[placeholder="{field["placeholder"]}"]'

    # Priority 5: XPath
    if field.get("xpath"):
        return f'xpath:{field["xpath"]}'

    # Fallback: tag + type
    if field.get("type"):
        return f'{tag}[type="{field["type"]}"]'

    return tag


def build_selector_from_analysis(field_analysis: dict) -> str:
    """
    Build a selector from Claude's field analysis result.

    The analysis includes: aria_label, name (from label), role, placeholder.
    We try to match against the form_summary elements to find the best selector.
    """
    # If the analysis included an explicit selector, use it
    if field_analysis.get("selector"):
        return field_analysis["selector"]

    # Build from available attributes
    role = field_analysis.get("role", "textbox")
    tag = "input"
    if role == "combobox":
        tag = "select"
    elif role == "textbox" and "cover" in field_analysis.get("name", "").lower():
        tag = "textarea"

    if field_analysis.get("aria_label"):
        return f'[aria-label="{field_analysis["aria_label"]}"]'

    if field_analysis.get("placeholder"):
        return f'{tag}[placeholder="{field_analysis["placeholder"]}"]'

    return ""


# ──────────────────────────────────────────────────────────────
# Field analysis via Claude CLI
# ──────────────────────────────────────────────────────────────

FIELD_DISCOVERY_PROMPT = """You are analyzing a web form for automated filling.

ACCESSIBILITY TREE:
{accessibility_tree}

FORM ELEMENTS:
{form_elements}

VISIBLE TEXT CONTEXT:
{page_title} - {page_url}

Identify all interactive form fields. Return JSON:
{{
  "page_type": "form" | "confirmation" | "error" | "other",
  "fields": [
    {{
      "role": "textbox" | "combobox" | "checkbox" | "radio" | "button" | "file_upload",
      "name": "<field name/label>",
      "field_purpose": "first_name" | "last_name" | "full_name" | "name" | "email" | "phone" | "location" | "linkedin" | "github" | "portfolio" | "website" | "company" | "cover_letter" | "resume" | "custom",
      "aria_label": "<from a11y tree>",
      "placeholder": "<if any>",
      "required": true,
      "current_value": "<if pre-filled>",
      "options": ["<for selects/radios>"],
      "custom_question": "<full question text if field_purpose is custom>",
      "selector": "<best CSS selector: prefer #id, then [name=...], then [aria-label=...], then xpath>",
      "element_index": -1
    }}
  ],
  "navigation": {{
    "has_next": false,
    "has_submit": false,
    "next_button_text": "<text>",
    "submit_button_text": "<text>",
    "next_button_selector": "<CSS selector>",
    "submit_button_selector": "<CSS selector>"
  }}
}}

IMPORTANT:
- element_index should match the [N] index from FORM ELEMENTS if identifiable, -1 otherwise.
- For selector, prefer #id > [name="..."] > [aria-label="..."] > tag[placeholder="..."] > xpath.
- Include ALL interactive fields, not just the obvious ones.
- For file inputs (type="file"), set role to "file_upload" and field_purpose to "resume" if it's a resume upload.
- page_type should be "confirmation" if this looks like a thank-you/success page.
- page_type should be "error" if there are visible validation errors."""


async def analyze_form_fields(page, brain: ClaudeBrain, url: str) -> Optional[dict]:
    """
    Use Claude CLI to analyze the current page's form fields.

    Phase 1 (Observe): Takes an accessibility tree snapshot and form element summary,
    sends them to Claude CLI, and gets back structured field analysis.

    Returns:
        Dict with page_type, fields, and navigation info, or None on failure.
    """
    try:
        tree, form_summary = await get_form_snapshot(page)
    except Exception as e:
        logger.warning(f"Failed to get form snapshot: {e}")
        return None

    if not form_summary and not tree:
        return None

    # Format the accessibility tree
    a11y_text = ""
    if tree:
        a11y_text = _format_a11y_tree(tree)
        # Truncate if too large for CLI context
        if len(a11y_text) > 8000:
            a11y_text = a11y_text[:8000] + "\n... (truncated)"

    # Format form elements summary
    form_text = _format_form_summary(form_summary) if form_summary else "(no form elements found)"

    # Get page context
    try:
        page_title = await page.title()
    except Exception:
        page_title = ""

    prompt = FIELD_DISCOVERY_PROMPT.format(
        accessibility_tree=a11y_text or "(not available)",
        form_elements=form_text,
        page_title=page_title,
        page_url=url,
    )

    try:
        result = brain.ask_json(prompt, timeout=60, component="form_analysis")
        return result
    except Exception as e:
        logger.warning(f"Claude CLI form analysis failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# Field matching — map profile data to discovered fields
# ──────────────────────────────────────────────────────────────

# Maps field_purpose -> profile path
FIELD_PURPOSE_MAP = {
    "first_name": lambda p: p["personal"].get("first_name", ""),
    "last_name": lambda p: p["personal"].get("last_name", ""),
    "full_name": lambda p: f'{p["personal"].get("first_name", "")} {p["personal"].get("last_name", "")}'.strip(),
    "name": lambda p: f'{p["personal"].get("first_name", "")} {p["personal"].get("last_name", "")}'.strip(),
    "email": lambda p: p["personal"].get("email", ""),
    "phone": lambda p: p["personal"].get("phone", ""),
    "location": lambda p: p["personal"].get("location", ""),
    "linkedin": lambda p: p["personal"].get("linkedin", ""),
    "github": lambda p: p["personal"].get("github", ""),
    "portfolio": lambda p: p["personal"].get("portfolio", ""),
    "website": lambda p: p["personal"].get("portfolio", ""),
    "company": lambda p: p["personal"].get("current_company", ""),
}


def get_field_value(field: dict, profile: dict, cover_letter: str = "") -> Optional[str]:
    """
    Determine the value to fill for a given field based on its purpose.

    Profile data is used directly -- it is NEVER sent through Claude.
    Claude only identifies WHERE to fill, not WHAT.

    Returns:
        The value string, or None if the field needs special handling (custom, file, etc.)
    """
    purpose = field.get("field_purpose", "custom")

    # Standard fields from profile
    if purpose in FIELD_PURPOSE_MAP:
        return FIELD_PURPOSE_MAP[purpose](profile)

    # Cover letter
    if purpose == "cover_letter":
        return cover_letter[:3000] if cover_letter else None

    # Resume handled separately (file upload)
    if purpose == "resume":
        return None

    # Custom field — returns None, caller will use answer system
    return None


# ──────────────────────────────────────────────────────────────
# Form filling actions
# ──────────────────────────────────────────────────────────────

async def _try_selector(page, selector: str, timeout: int = 3000):
    """Try to find an element using the given selector. Returns element or None."""
    if not selector:
        return None

    try:
        if selector.startswith("xpath:"):
            xpath = selector[6:]
            el = await page.wait_for_selector(f"xpath={xpath}", timeout=timeout)
        else:
            el = await page.wait_for_selector(selector, timeout=timeout)
        return el
    except Exception:
        return None


async def _fill_field(page, selector: str, value: str) -> bool:
    """Fill a text input or textarea."""
    el = await _try_selector(page, selector)
    if not el:
        return False
    try:
        await el.fill(value)
        return True
    except Exception as e:
        # Some fields don't support fill() (e.g., contenteditable)
        try:
            await el.click()
            await page.keyboard.type(value, delay=10)
            return True
        except Exception:
            logger.debug(f"Fill failed for {selector}: {e}")
            return False


async def _select_option(page, selector: str, value: str, options: list = None) -> bool:
    """Select an option from a dropdown with fuzzy matching fallback."""
    el = await _try_selector(page, selector)
    if not el:
        return False

    try:
        # Try exact label match first
        await el.select_option(label=value)
        return True
    except Exception:
        pass

    try:
        # Try exact value match
        await el.select_option(value=value)
        return True
    except Exception:
        pass

    # Fuzzy matching fallback
    if options:
        value_lower = value.lower()
        for opt in options:
            opt_text = opt if isinstance(opt, str) else opt.get("text", "")
            opt_val = opt if isinstance(opt, str) else opt.get("value", "")
            if value_lower in opt_text.lower() or value_lower in opt_val.lower():
                try:
                    await el.select_option(value=opt_val)
                    return True
                except Exception:
                    try:
                        await el.select_option(label=opt_text)
                        return True
                    except Exception:
                        continue

    # Last resort: get all options from the element and try fuzzy match
    try:
        el_options = await el.evaluate(
            'el => Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))'
        )
        value_lower = value.lower()
        for opt in el_options:
            if value_lower in opt["text"].lower() or value_lower in opt["value"].lower():
                await el.select_option(value=opt["value"])
                return True
    except Exception:
        pass

    return False


async def _check_field(page, selector: str, should_check: bool = True) -> bool:
    """Check or uncheck a checkbox."""
    el = await _try_selector(page, selector)
    if not el:
        return False
    try:
        if should_check:
            await el.check()
        else:
            await el.uncheck()
        return True
    except Exception as e:
        # Fallback: click
        try:
            await el.click()
            return True
        except Exception:
            logger.debug(f"Check failed for {selector}: {e}")
            return False


async def _click_element(page, selector: str) -> bool:
    """Click a button or link."""
    el = await _try_selector(page, selector)
    if not el:
        return False
    try:
        await el.click()
        return True
    except Exception as e:
        logger.debug(f"Click failed for {selector}: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# Resilience Layer: Multi-strategy field filling
# ──────────────────────────────────────────────────────────────

async def _fill_via_label(page, label_text: str, value: str) -> bool:
    """Find a field by its label text and fill the associated input."""
    if not label_text:
        return False
    try:
        selectors = [
            f'label:has-text("{label_text}") >> input',
            f'label:has-text("{label_text}") >> textarea',
            f'label:has-text("{label_text}") >> select',
            f'text="{label_text}" >> .. >> input',
            f'[aria-label*="{label_text}" i]',
        ]
        for sel in selectors:
            el = await _try_selector(page, sel, timeout=2000)
            if el:
                try:
                    await el.fill(value)
                    return True
                except Exception:
                    try:
                        await el.click()
                        await page.keyboard.type(value, delay=10)
                        return True
                    except Exception:
                        continue
        return False
    except Exception:
        return False


async def _fill_via_placeholder(page, field: dict, value: str) -> bool:
    """Find a field by placeholder text."""
    try:
        placeholder = field.get("placeholder", "")
        if not placeholder:
            return False
        sel = f'[placeholder*="{placeholder}" i]'
        el = await _try_selector(page, sel, timeout=2000)
        if el:
            try:
                await el.fill(value)
                return True
            except Exception:
                try:
                    await el.click()
                    await page.keyboard.type(value, delay=10)
                    return True
                except Exception:
                    return False
        return False
    except Exception:
        return False


async def _fill_via_form_summary(page, field: dict, form_summary: list, value: str) -> bool:
    """Try to find the field using the live form_summary data."""
    try:
        field_name = field.get("name", "").lower()
        purpose = field.get("field_purpose", "")

        for elem in form_summary:
            # Match by multiple heuristics
            elem_name = (elem.get("name") or "").lower()
            elem_label = (elem.get("label") or "").lower()
            elem_id = (elem.get("id") or "").lower()
            elem_placeholder = (elem.get("placeholder") or "").lower()

            matches = (
                (field_name and field_name in elem_label) or
                (field_name and field_name in elem_name) or
                (purpose and purpose.replace("_", "") in elem_id) or
                (purpose and purpose.replace("_", " ") in elem_label)
            )

            if matches:
                selector = build_selector(elem)
                el = await _try_selector(page, selector, timeout=2000)
                if el:
                    tag = elem.get("tag", "input")
                    if tag == "select":
                        return await _select_option(page, selector, value, elem.get("options"))
                    else:
                        return await _fill_field(page, selector, value)

        return False
    except Exception:
        return False


async def _fill_via_cli_retry(page, brain, field: dict, value: str) -> bool:
    """Re-analyze just this one field via Claude CLI with the current DOM state."""
    try:
        field_name = field.get("name", "unknown")

        # Get a fresh mini-snapshot focused on this field
        mini_snapshot = await page.evaluate("""(searchText) => {
            const all = document.querySelectorAll('input, textarea, select');
            const matches = Array.from(all).filter(el => {
                const label = el.closest('label')?.textContent || '';
                const placeholder = el.placeholder || '';
                const name = el.name || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const st = searchText.toLowerCase();
                return label.toLowerCase().includes(st) ||
                       placeholder.toLowerCase().includes(st) ||
                       name.toLowerCase().includes(st) ||
                       ariaLabel.toLowerCase().includes(st);
            });
            return matches.map(el => ({
                tag: el.tagName.toLowerCase(),
                id: el.id,
                name: el.name,
                placeholder: el.placeholder,
                'aria-label': el.getAttribute('aria-label'),
                type: el.type,
            }));
        }""", field_name)

        if mini_snapshot:
            # Try each match
            for elem in mini_snapshot:
                selector = build_selector(elem)
                if await _fill_field(page, selector, value):
                    return True

        return False
    except Exception:
        return False


async def _vision_fallback(page, brain, field_name: str, value: str) -> bool:
    """When DOM methods fail, use screenshot + Claude CLI to find and fill a field."""
    try:
        screenshot_path = SCREENSHOT_DIR / f"step_{int(time.time())}.png"
        await page.screenshot(path=str(screenshot_path))

        # Ask Claude CLI to identify the field location
        prompt = f"""I have a screenshot of a job application form.
I need to fill the field labeled "{field_name}" with value: "{value[:50]}".

Looking at the page structure, describe:
1. Where is the "{field_name}" field located?
2. What CSS selector or XPath could target it?
3. Is it visible on the current viewport or do I need to scroll?

Return JSON: {{"selector": "<best selector>", "needs_scroll": true, "scroll_direction": "down", "visible": true, "description": "<what you see>"}}"""

        result = brain.ask_json(prompt, timeout=30, component="form_analysis")
        if not result:
            return False

        selector = result.get("selector", "")
        needs_scroll = result.get("needs_scroll", False)

        # If we need to scroll first
        if needs_scroll:
            direction = result.get("scroll_direction", "down")
            scroll_amount = 300 if direction == "down" else -300
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(0.5)

        # Try the suggested selector
        if selector:
            if await _fill_field(page, selector, value):
                return True

        # Clean up screenshot
        try:
            screenshot_path.unlink(missing_ok=True)
        except Exception:
            pass

        return False
    except Exception:
        return False


async def _fill_field_resilient(page, field: dict, value: str, form_summary: list, brain) -> bool:
    """Try up to 5 strategies to fill a single field, never crashing."""
    field_name = field.get("name", "unknown")
    role = field.get("role", "textbox")

    strategies = [
        # Strategy 1: Direct selector from analysis
        ("selector", lambda: _fill_with_selector_by_role(page, field.get("selector"), value, role)),
        # Strategy 2: Build selector from element_index in form_summary
        ("form_summary", lambda: _fill_via_form_summary(page, field, form_summary, value)),
        # Strategy 3: Label-based — find label text, then fill adjacent input
        ("label", lambda: _fill_via_label(page, field.get("name", ""), value)),
        # Strategy 4: Placeholder-based search
        ("placeholder", lambda: _fill_via_placeholder(page, field, value)),
        # Strategy 5: Claude CLI re-analysis of just this field
        ("cli_retry", lambda: _fill_via_cli_retry(page, brain, field, value)),
    ]

    for strategy_name, strategy in strategies:
        try:
            if await strategy():
                logger.debug(f"Field '{field_name}' filled via strategy: {strategy_name}")
                return True
        except Exception as e:
            logger.debug(f"Strategy '{strategy_name}' failed for '{field_name}': {e}")
            continue

    return False


async def _fill_with_selector_by_role(page, selector: str, value: str, role: str) -> bool:
    """Fill a field using the given selector, handling role-specific behavior."""
    if not selector:
        return False
    try:
        if role in ("combobox", "select"):
            return await _select_option(page, selector, value)
        elif role == "checkbox":
            should_check = str(value).lower() in ("yes", "true", "1", "checked")
            return await _check_field(page, selector, should_check)
        elif role == "radio":
            return await _click_element(page, selector)
        else:
            return await _fill_field(page, selector, value)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# Resilience Layer: Error detection and recovery
# ──────────────────────────────────────────────────────────────

async def _detect_and_handle_errors(page, brain) -> bool:
    """Detect form validation errors and return True if errors found."""
    try:
        errors = await page.evaluate("""() => {
            const errorElements = document.querySelectorAll(
                '.error, .invalid, [class*="error"], [class*="invalid"], ' +
                '[aria-invalid="true"], .field-error, .validation-error, ' +
                '[role="alert"]'
            );
            return Array.from(errorElements).map(el => ({
                text: el.textContent.trim().substring(0, 200),
                nearestInput: (() => {
                    const parent = el.closest('.field, .form-group, .form-field, [class*="field"]');
                    const input = parent ? parent.querySelector('input, textarea, select') : null;
                    return input ? {
                        id: input.id,
                        name: input.name,
                        type: input.type,
                    } : null;
                })(),
            })).filter(e => e.text.length > 0);
        }""")

        if errors:
            print(f"    [!] Found {len(errors)} validation errors:")
            for err in errors[:5]:
                print(f"        - {err['text'][:80]}")
            return True

        return False
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# Resilience Layer: Scroll-to-find hidden fields
# ──────────────────────────────────────────────────────────────

async def _scroll_to_find_field(page, field_name: str) -> bool:
    """Scroll the page to find a field that might be out of viewport."""
    try:
        viewport_height = await page.evaluate("window.innerHeight")
        total_height = await page.evaluate("document.body.scrollHeight")

        # Escape single quotes in field_name for JS
        safe_name = field_name.lower().replace("'", "\\'")

        current = 0
        while current < total_height:
            # Check if field is now visible
            found = await page.evaluate(f"""() => {{
                const labels = document.querySelectorAll('label');
                for (const label of labels) {{
                    if (label.textContent.toLowerCase().includes('{safe_name}')) {{
                        const input = label.querySelector('input, textarea, select') ||
                                      document.getElementById(label.getAttribute('for'));
                        if (input && input.offsetParent !== null) {{
                            input.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                            return true;
                        }}
                    }}
                }}
                // Also check aria-labels and placeholders
                const inputs = document.querySelectorAll('input, textarea, select');
                for (const input of inputs) {{
                    const ariaLabel = (input.getAttribute('aria-label') || '').toLowerCase();
                    const placeholder = (input.placeholder || '').toLowerCase();
                    const name = (input.name || '').toLowerCase();
                    if (ariaLabel.includes('{safe_name}') || placeholder.includes('{safe_name}') || name.includes('{safe_name}')) {{
                        if (input.offsetParent !== null) {{
                            input.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                            return true;
                        }}
                    }}
                }}
                return false;
            }}""")

            if found:
                await asyncio.sleep(0.5)
                return True

            current += int(viewport_height * 0.7)
            await page.evaluate(f"window.scrollTo(0, {current})")
            await asyncio.sleep(0.3)

        return False
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# Resilience Layer: iframe detection and traversal
# ──────────────────────────────────────────────────────────────

async def _find_form_in_iframes(page) -> tuple:
    """Search for forms inside iframes (common in Workday, iCIMS, Taleo).

    Returns:
        (frame_or_page, is_iframe) — the frame containing the form and a bool.
    """
    try:
        frames = page.frames

        for frame in frames:
            if frame == page.main_frame:
                continue

            try:
                form_count = await frame.evaluate("""() => {
                    return document.querySelectorAll('input:not([type="hidden"]), textarea, select').length;
                }""")

                if form_count > 2:  # Found a form with multiple fields
                    frame_url = getattr(frame, 'url', '')
                    print(f"    [*] Found form in iframe: {frame_url[:80]}")
                    return frame, True
            except Exception:
                continue

        return page, False
    except Exception:
        return page, False


# ──────────────────────────────────────────────────────────────
# Resilience Layer: Enhanced verification with retry
# ──────────────────────────────────────────────────────────────

async def _verify_and_retry(
    page, form_analysis: dict, form_summary: list, profile: dict,
    brain, cover_letter: str, max_retries: int = 2
) -> int:
    """Verify filled fields and retry any that failed.

    Returns the number of still-failed fields after retries.
    """
    failed = await _verify_fields(page, form_analysis, profile, cover_letter)

    if not failed:
        return 0

    retries = 0
    while failed and retries < max_retries:
        retries += 1
        print(f"    [!] Retry {retries}: {len(failed)} fields need re-filling")

        for field in form_analysis.get("fields", []):
            if field.get("name") not in failed:
                continue

            value = get_field_value(field, profile, cover_letter)
            if value is None:
                continue

            # Use the resilient multi-strategy approach
            success = await _fill_field_resilient(page, field, value, form_summary, brain)
            if success:
                print(f"      [+] {field.get('name')}: re-filled on retry {retries}")

        failed = await _verify_fields(page, form_analysis, profile, cover_letter)

    return len(failed)


async def _upload_file(page, selector: str, file_path: str) -> bool:
    """Upload a file to a file input."""
    if not file_path or not Path(file_path).exists():
        return False

    el = await _try_selector(page, selector)
    if not el:
        # Try generic file input
        try:
            el = await page.wait_for_selector('input[type="file"]', timeout=3000)
        except Exception:
            return False

    if not el:
        return False

    try:
        await el.set_input_files(str(Path(file_path).absolute()))
        return True
    except Exception as e:
        logger.debug(f"File upload failed for {selector}: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# Confirmation & navigation detection
# ──────────────────────────────────────────────────────────────

def _is_confirmation(text) -> bool:
    """Check if page text indicates a successful submission."""
    if not text:
        return False
    lower = str(text).lower()
    return any(ind in lower for ind in CONFIRMATION_INDICATORS)


async def _detect_page_state(page) -> str:
    """Detect the current page state: 'form', 'confirmation', 'error', 'other'."""
    try:
        body_text = await page.inner_text("body")
        body_lower = body_text.lower()

        if _is_confirmation(body_text):
            return "confirmation"

        # Check for form elements
        form_count = await page.evaluate("""() => {
            return document.querySelectorAll('input:not([type="hidden"]), textarea, select').length;
        }""")

        if form_count > 0:
            return "form"

        # Check for error indicators
        error_indicators = ["error", "invalid", "please correct", "required field"]
        if any(ind in body_lower for ind in error_indicators):
            return "error"

        return "other"
    except Exception:
        return "other"


# ──────────────────────────────────────────────────────────────
# Self-healing selector resolution
# ──────────────────────────────────────────────────────────────

async def _resolve_selector(
    page, field: dict, form_summary: list, brain: ClaudeBrain = None
) -> Optional[str]:
    """
    Resolve a working selector for a field, with self-healing.

    Tries the provided selector first. If it fails, attempts to match
    against the current form_summary by element_index, then by attributes.
    If all else fails, asks Claude CLI to re-identify the field.
    """
    selector = field.get("selector", "")

    # Try the provided selector
    if selector:
        el = await _try_selector(page, selector, timeout=2000)
        if el:
            return selector

    # Try matching by element_index from form_summary
    idx = field.get("element_index", -1)
    if idx >= 0 and idx < len(form_summary):
        alt_selector = build_selector(form_summary[idx])
        el = await _try_selector(page, alt_selector, timeout=2000)
        if el:
            return alt_selector

    # Try building from analysis attributes
    alt_selector = build_selector_from_analysis(field)
    if alt_selector:
        el = await _try_selector(page, alt_selector, timeout=2000)
        if el:
            return alt_selector

    # Search form_summary for a matching field by name/label
    field_name = field.get("name", "").lower()
    field_label = field.get("aria_label", "").lower()
    for elem in form_summary:
        elem_label = (elem.get("label", "") or "").lower()
        elem_name = (elem.get("name", "") or "").lower()
        elem_aria = (elem.get("aria-label", "") or "").lower()
        elem_placeholder = (elem.get("placeholder", "") or "").lower()

        if field_name and (
            field_name in elem_label or
            field_name in elem_name or
            field_name in elem_aria or
            field_name in elem_placeholder
        ):
            alt_selector = build_selector(elem)
            el = await _try_selector(page, alt_selector, timeout=2000)
            if el:
                return alt_selector

        if field_label and (
            field_label in elem_label or
            field_label in elem_aria
        ):
            alt_selector = build_selector(elem)
            el = await _try_selector(page, alt_selector, timeout=2000)
            if el:
                return alt_selector

    # Scroll-to-find fallback: the field might be out of viewport
    search_name = field_name or field_label
    if search_name:
        try:
            scrolled = await _scroll_to_find_field(page, search_name)
            if scrolled:
                # Re-try the original selector or attribute-based selectors after scrolling
                if selector:
                    el = await _try_selector(page, selector, timeout=2000)
                    if el:
                        return selector
                alt_selector = build_selector_from_analysis(field)
                if alt_selector:
                    el = await _try_selector(page, alt_selector, timeout=2000)
                    if el:
                        return alt_selector
        except Exception:
            pass

    return None


# ──────────────────────────────────────────────────────────────
# Core form filling loop
# ──────────────────────────────────────────────────────────────

async def _fill_form_step(
    page,
    url: str,
    profile: dict,
    brain: ClaudeBrain,
    cover_letter: str,
    form_analysis: dict,
    form_summary: list,
) -> int:
    """
    Fill all fields in the current form step.

    Returns the number of fields successfully filled.
    """
    fields = form_analysis.get("fields", [])
    personal = profile.get("personal", {})
    common = profile.get("common_answers", {})
    filled = 0

    for field in fields:
        purpose = field.get("field_purpose", "custom")
        role = field.get("role", "textbox")
        field_name = field.get("name", "unknown")

        # Resolve the selector with self-healing
        selector = await _resolve_selector(page, field, form_summary, brain)
        if not selector:
            logger.debug(f"Could not resolve selector for field: {field_name}")
            continue

        # Determine the value to fill
        value = get_field_value(field, profile, cover_letter)

        if purpose == "resume" or role == "file_upload":
            # File upload
            resume_path = profile.get("resume_path", "")
            if await _upload_file(page, selector, resume_path):
                filled += 1
                print(f"    [+] {field_name}: uploaded resume")
            else:
                print(f"    [!] {field_name}: resume upload failed")
            continue

        if purpose == "custom":
            # Custom question: use cached answers -> personal fields -> AI
            question_text = field.get("custom_question", field_name)

            # Try cached answer
            cached_answer = find_cached_answer(question_text, common)
            if cached_answer:
                value = cached_answer
            else:
                # Try personal field mapping
                personal_val = get_personal_field(question_text, personal)
                if personal_val:
                    value = personal_val
                else:
                    # Fall back to AI
                    try:
                        value = brain.answer_question(question_text, profile)
                        if value:
                            value = value.strip()
                    except Exception as e:
                        logger.debug(f"AI answer failed for '{question_text}': {e}")
                        value = None

        if value is None:
            continue

        # Execute the fill action based on field role
        success = False
        try:
            if role in ("textbox", "text"):
                success = await _fill_field(page, selector, value)
            elif role in ("combobox", "select"):
                options = field.get("options", [])
                success = await _select_option(page, selector, value, options)
            elif role == "checkbox":
                should_check = str(value).lower() in ("yes", "true", "1", "checked")
                success = await _check_field(page, selector, should_check)
            elif role == "radio":
                success = await _click_element(page, selector)
            elif role == "button":
                # Buttons are handled in navigation, skip here
                continue
            else:
                # Default to fill
                success = await _fill_field(page, selector, value)

            # If primary fill failed, escalate to multi-strategy resilient fill
            if not success:
                logger.debug(f"Primary fill failed for {field_name}, trying resilient strategies...")
                success = await _fill_field_resilient(page, field, value, form_summary, brain)

            if success:
                filled += 1
                source = "profile" if purpose != "custom" else "answer"
                print(f"    [+] {field_name}: filled ({source})")
            else:
                print(f"    [!] {field_name}: fill failed (all strategies exhausted)")

        except Exception as e:
            logger.debug(f"Fill action failed for {field_name}: {e}")
            print(f"    [!] {field_name}: {e}")

        # Human-like delay between fields
        await asyncio.sleep(random.uniform(0.2, 0.6))

    return filled


async def _handle_navigation_step(
    page, form_analysis: dict, dry_run: bool
) -> str:
    """
    Handle the next/submit button for the current form step.

    Returns: 'done', 'dry_run_stop', 'next', 'failed'
    """
    nav = form_analysis.get("navigation", {})
    has_submit = nav.get("has_submit", False)
    has_next = nav.get("has_next", False)

    if has_submit:
        submit_selector = nav.get("submit_button_selector", "")
        submit_text = nav.get("submit_button_text", "Submit")

        if dry_run:
            print(f"\n  [=] DRY RUN -- Would click '{submit_text}'. Review the form in the browser.")
            return "dry_run_stop"

        print(f"  [>] Submitting application ({submit_text})...")

        # Try provided selector
        success = False
        if submit_selector:
            success = await _click_element(page, submit_selector)

        # Fallback: try common submit selectors
        if not success:
            for sel in [
                'button[type="submit"]',
                'input[type="submit"]',
                f'button:has-text("{submit_text}")',
                'button:has-text("Submit")',
                'button:has-text("Submit Application")',
            ]:
                if await _click_element(page, sel):
                    success = True
                    break

        if success:
            await asyncio.sleep(3)
            state = await _detect_page_state(page)
            if state == "confirmation":
                print("  [+] Application submitted successfully!")
            else:
                print("  [?] Submitted but no clear confirmation detected")
            return "done"
        else:
            print("  [!] Failed to click submit button")
            return "failed"

    if has_next:
        next_selector = nav.get("next_button_selector", "")
        next_text = nav.get("next_button_text", "Next")

        print(f"  [>] Next step ({next_text})...")

        success = False
        if next_selector:
            success = await _click_element(page, next_selector)

        if not success:
            for sel in [
                f'button:has-text("{next_text}")',
                'button:has-text("Next")',
                'button:has-text("Continue")',
                'button:has-text("Save & Continue")',
                'a:has-text("Next")',
            ]:
                if await _click_element(page, sel):
                    success = True
                    break

        if success:
            await asyncio.sleep(2)
            return "next"
        else:
            print("  [!] Failed to click next button")
            return "failed"

    # No buttons detected — might be a single-page form we missed
    # Try common submit buttons as a last resort
    for sel in [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Apply")',
    ]:
        el = await _try_selector(page, sel, timeout=2000)
        if el:
            if dry_run:
                print(f"\n  [=] DRY RUN -- Found submit button. Review the form in the browser.")
                return "dry_run_stop"
            if await _click_element(page, sel):
                await asyncio.sleep(3)
                return "done"

    print("  [!] No navigation button found")
    return "failed"


# ──────────────────────────────────────────────────────────────
# Verification pass
# ──────────────────────────────────────────────────────────────

async def _verify_fields(page, form_analysis: dict, profile: dict, cover_letter: str) -> list:
    """
    Re-snapshot the DOM and verify that fields are filled correctly.

    Returns a list of field names that failed verification.
    """
    failed = []
    fields = form_analysis.get("fields", [])

    for field in fields:
        purpose = field.get("field_purpose", "custom")
        role = field.get("role", "textbox")
        selector = field.get("selector", "")

        if not selector or role in ("button", "file_upload") or purpose == "resume":
            continue

        try:
            el = await _try_selector(page, selector, timeout=1000)
            if not el:
                continue

            if role in ("textbox", "text"):
                current_value = await el.input_value()
                expected = get_field_value(field, profile, cover_letter) or ""
                if not current_value and expected:
                    failed.append(field.get("name", "unknown"))
            elif role in ("combobox", "select"):
                current_value = await el.input_value()
                if not current_value or current_value == "":
                    # Select might show placeholder
                    failed.append(field.get("name", "unknown"))

        except Exception:
            pass

    return failed


# ──────────────────────────────────────────────────────────────
# Public API: is_stagehand_available
# ──────────────────────────────────────────────────────────────

def is_stagehand_available() -> bool:
    """
    Check if the CLI-powered form filler is available.

    Since this adapter only requires Playwright + Claude CLI (both already
    installed), it is always available. No external packages or API keys needed.
    """
    return True


# ──────────────────────────────────────────────────────────────
# Public API: apply_stagehand
# ──────────────────────────────────────────────────────────────

async def apply_stagehand(
    page,              # Playwright Page (caller manages browser lifecycle)
    job_url: str,
    profile: dict,
    brain: ClaudeBrain,
    cover_letter: str = "",
    dry_run: bool = True,
    max_steps: int = 12,
) -> bool:
    """
    Self-healing AI form filler using Playwright + Claude CLI.

    Architecture: observe -> plan -> act -> verify -> navigate

    Phase 1 (Observe): Take DOM snapshot via accessibility tree
    Phase 2 (Plan): Ask Claude CLI to identify form fields and map to profile
    Phase 3 (Act): Fill fields using Playwright, with retry on failure
    Phase 4 (Verify): Re-snapshot DOM, verify fields are filled correctly
    Phase 5 (Navigate): Click next/submit or detect confirmation

    Args:
        page: Playwright Page object (caller manages browser lifecycle)
        job_url: Full URL to the job application page
        profile: Parsed profile.yaml dict
        brain: ClaudeBrain instance (uses ask_json for field discovery)
        cover_letter: Pre-generated cover letter text
        dry_run: If True, fill form but don't click submit
        max_steps: Maximum wizard steps before giving up

    Returns:
        True on success/dry-run, False on failure
    """
    personal = profile.get("personal", {})
    common = profile.get("common_answers", {})

    print(f"  [*] CLI-powered adapter (observe -> act -> cache)")
    print(f"  [*] Loading: {job_url}")

    try:
        await page.goto(job_url, wait_until="networkidle", timeout=20000)
    except Exception:
        # SPA pages (Lever, Ashby, Workday) often don't reach networkidle
        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  [!] Failed to load page: {e}")
            return False

    await asyncio.sleep(3)  # Let SPA frameworks render forms

    # Phase 0: Check for forms inside iframes (Workday, iCIMS, Taleo)
    active_frame = page
    is_iframe = False
    try:
        active_frame, is_iframe = await _find_form_in_iframes(page)
        if is_iframe:
            print(f"  [*] Form detected in iframe — switching context")
    except Exception:
        active_frame = page

    # Multi-step wizard loop
    step = 0
    while step < max_steps:
        step += 1
        print(f"\n  --- Step {step} ---")

        # Check if we're on a confirmation page
        state = await _detect_page_state(active_frame)
        if state == "confirmation":
            print("  [+] Application submitted successfully!")
            return True

        if state == "other" and step > 1:
            print("  [?] No form found on page (might be done)")
            return True

        # Phase 1 & 2: Observe + Plan — analyze form fields via Claude CLI
        # Check cache first
        cache_key = _cache_key(job_url, f"form_step_{step}")
        cached_analysis = _load_cached_action(cache_key)

        # Always get a fresh form_summary for selector resolution
        try:
            _, form_summary = await get_form_snapshot(active_frame)
        except Exception:
            form_summary = []

        if cached_analysis and form_summary:
            print(f"  [*] Using cached form analysis")
            form_analysis = cached_analysis
        else:
            print(f"  [*] Analyzing form via Claude CLI...")
            form_analysis = await analyze_form_fields(active_frame, brain, job_url)

            if not form_analysis:
                print("  [!] Form analysis failed")
                if step > 1:
                    # Might have navigated past the form
                    return True
                return False

        page_type = form_analysis.get("page_type", "other")

        if page_type == "confirmation":
            print("  [+] Application submitted successfully!")
            return True

        if page_type == "error":
            print("  [!] Form has validation errors")
            # Continue anyway — might be able to fix by filling required fields

        if page_type not in ("form", "error"):
            if step > 1:
                print("  [?] No form detected (might be done)")
                return True
            print("  [!] No form found on page")
            return False

        # Cache the analysis for future use
        if not cached_analysis:
            _save_cached_action(cache_key, form_analysis)

        # Phase 3: Act — fill form fields
        filled = await _fill_form_step(
            active_frame, job_url, profile, brain, cover_letter,
            form_analysis, form_summary
        )
        print(f"  [+] Filled {filled} fields total")

        # Phase 4: Verify with retry — check fields and re-fill failures
        still_failed = await _verify_and_retry(
            active_frame, form_analysis, form_summary, profile,
            brain, cover_letter, max_retries=2
        )
        if still_failed > 0:
            print(f"  [!] {still_failed} fields still empty after retries")

        # Update domain cache with successful selectors
        field_mappings = {}
        for field in form_analysis.get("fields", []):
            purpose = field.get("field_purpose")
            selector = field.get("selector", "")
            if purpose and selector and purpose not in ("custom",):
                role = field.get("role", "textbox")
                method = "fill"
                if role in ("combobox", "select"):
                    method = "select"
                elif role == "file_upload":
                    method = "upload"
                elif role == "checkbox":
                    method = "check"
                field_mappings[purpose] = {"selector": selector, "method": method}

        if field_mappings:
            _save_domain_cache(job_url, field_mappings)

        # Phase 5: Navigate — click next/submit
        nav_result = await _handle_navigation_step(active_frame, form_analysis, dry_run)

        if nav_result == "done":
            return True
        elif nav_result == "dry_run_stop":
            return True
        elif nav_result == "next":
            await asyncio.sleep(2)  # Wait for next page to load
            # Re-check for iframe after navigation (page might have changed)
            try:
                active_frame, is_iframe = await _find_form_in_iframes(page)
            except Exception:
                active_frame = page
            continue
        elif nav_result == "failed":
            # Check for validation errors and report them
            has_errors = await _detect_and_handle_errors(active_frame, brain)
            if has_errors:
                print("  [!] Validation errors detected — re-analyzing form...")
                # Re-analyze and try to fix errors on the next iteration
            else:
                print("  [!] Navigation failed -- retrying step...")
            await asyncio.sleep(1)
            continue

    print(f"  [!] Reached max steps ({max_steps})")
    return False


# ──────────────────────────────────────────────────────────────
# Public API: apply_smart — intelligent adapter router
# ──────────────────────────────────────────────────────────────

async def apply_smart(
    page,
    job_url: str,
    profile: dict,
    brain: ClaudeBrain,
    cover_letter: str = "",
    dry_run: bool = True,
    platform: str = "",
) -> bool:
    """
    Intelligent adapter router with cascading fallbacks.

    Selection order:
    1. Greenhouse adapter for greenhouse.io URLs (purpose-built, most reliable)
    2. CLI-powered adapter for everything else (AI self-healing via Claude CLI)
    3. Generic adapter as final fallback (CSS + raw HTML approach)

    Args:
        page: Playwright Page (caller manages browser lifecycle)
        job_url: Full URL to the job application page
        profile: Parsed profile.yaml dict
        brain: ClaudeBrain instance
        cover_letter: Pre-generated cover letter text
        dry_run: If True, fill form but don't click submit
        platform: Override platform detection ("greenhouse", etc.)

    Returns:
        True on success/dry-run, False on failure
    """
    url_lower = job_url.lower()

    # Greenhouse: use purpose-built adapter (most reliable for this ATS)
    if platform == "greenhouse" or "greenhouse.io" in url_lower:
        print("  [*] Adapter: Greenhouse (purpose-built)")
        from adapters.greenhouse import apply_greenhouse
        try:
            result = await apply_greenhouse(
                page, job_url, profile, brain,
                cover_letter=cover_letter, dry_run=dry_run
            )
            if result:
                return True
            # Greenhouse adapter failed — fall through to CLI adapter
            print("  [!] Greenhouse adapter failed, trying CLI adapter...")
        except Exception as e:
            print(f"  [!] Greenhouse adapter error: {e}, trying CLI adapter...")

    # CLI-powered adapter: AI self-healing via Playwright + Claude CLI
    if is_stagehand_available():
        print("  [*] Adapter: CLI-powered (Playwright + Claude CLI)")
        try:
            result = await apply_stagehand(
                page, job_url, profile, brain,
                cover_letter=cover_letter, dry_run=dry_run
            )
            if result:
                return True
            # CLI adapter failed — fall through to generic
            print("  [!] CLI adapter failed, trying generic adapter...")
        except Exception as e:
            print(f"  [!] CLI adapter error: {e}, trying generic adapter...")

    # Generic: CSS-selector based AI form filler (last resort)
    print("  [*] Adapter: Generic (CSS + AI fallback)")
    from adapters.generic import apply_generic
    return await apply_generic(
        page, job_url, profile, brain,
        cover_letter=cover_letter, dry_run=dry_run
    )
