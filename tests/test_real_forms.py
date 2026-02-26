#!/usr/bin/env python3
"""
Real-form integration tests for the CLI-powered Stagehand adapter.

These tests navigate to actual ATS application pages and validate that
get_form_snapshot() and analyze_form_fields() correctly identify form fields
across different platforms.

Usage:
    # Run all platforms (requires internet + Playwright browsers installed)
    pytest tests/test_real_forms.py -v --timeout=120

    # Run a specific platform
    pytest tests/test_real_forms.py -v -k greenhouse --timeout=120

    # Run with visible browser
    HEADLESS=0 pytest tests/test_real_forms.py -v --timeout=120

    # Generate a report
    python tests/test_real_forms.py --report

NOTE: These tests do NOT submit any applications. They only navigate to public
      job posting pages, capture form snapshots, and validate field detection.
      All pages are public/unauthenticated application forms.
"""

import os
import sys
import json
import asyncio
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.stagehand_adapter import (
    get_form_snapshot,
    analyze_form_fields,
    _format_a11y_tree,
    _format_form_summary,
    build_selector,
    get_field_value,
    _find_form_in_iframes,
)

logger = logging.getLogger("test_real_forms")

# ──────────────────────────────────────────────────────────────
# Test fixtures and configuration
# ──────────────────────────────────────────────────────────────

# Map of ATS platforms → test URLs
# These are real public job application pages.
# If a URL goes stale, replace it with another from the same ATS.
ATS_TEST_URLS = {
    "greenhouse": {
        "url": "https://job-boards.greenhouse.io/anthropic/jobs/4613568008",
        "apply_url": "https://job-boards.greenhouse.io/anthropic/jobs/4613568008",
        "expected_fields": ["first_name", "last_name", "email", "phone", "resume"],
        "platform": "Greenhouse",
        "notes": "Standard Greenhouse form — First/Last Name, Email, Phone, Resume, Cover Letter, LinkedIn, GitHub, custom questions",
    },
    "lever": {
        "url": "https://jobs.lever.co/levelai/97951083-5465-4382-bb4d-ac9d89458a21/apply",
        "apply_url": "https://jobs.lever.co/levelai/97951083-5465-4382-bb4d-ac9d89458a21/apply",
        "expected_fields": ["first_name", "email", "phone", "resume", "linkedin"],
        "platform": "Lever",
        "notes": "Lever form — Name, Email, Phone, Resume, LinkedIn, GitHub, Portfolio, Cover Letter",
    },
    "ashby": {
        "url": "https://jobs.ashbyhq.com/benchling/b3c9b312-6e2b-4dbc-9b15-0b0310d75a7f/application",
        "apply_url": "https://jobs.ashbyhq.com/benchling/b3c9b312-6e2b-4dbc-9b15-0b0310d75a7f/application",
        "expected_fields": ["first_name", "email", "phone", "resume", "linkedin"],
        "platform": "Ashby",
        "notes": "Ashby SPA — Name, Email, Phone, Resume, LinkedIn, Location, custom questions. Can be slow to render.",
    },
}

# The standard fields every ATS should have (at minimum)
UNIVERSAL_FIELDS = {"first_name", "last_name", "email"}

# Mock profile for field value mapping tests
MOCK_PROFILE = {
    "personal": {
        "first_name": "Test",
        "last_name": "Applicant",
        "email": "test@example.com",
        "phone": "555-123-4567",
        "location": "San Francisco, CA",
        "linkedin": "https://linkedin.com/in/testapplicant",
        "github": "https://github.com/testapplicant",
        "portfolio": "https://testapplicant.dev",
    },
    "resume_path": "",
    "common_answers": {},
    "preferences": {"roles": ["Software Engineer"]},
    "skills": {"primary": ["Python", "JavaScript"]},
}


# ──────────────────────────────────────────────────────────────
# Pytest fixtures
# ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    """Create a module-scoped event loop for all async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def browser_context():
    """Create a shared browser context for all tests in this module."""
    from playwright.async_api import async_playwright

    headless = os.environ.get("HEADLESS", "1") != "0"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        yield context
        await browser.close()


@pytest.fixture
async def page(browser_context):
    """Create a fresh page for each test."""
    page = await browser_context.new_page()
    yield page
    await page.close()


# ──────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────

async def navigate_to_apply(page, url: str, max_retries: int = 2) -> bool:
    """Navigate to a URL with retries and wait for the page to settle."""
    for attempt in range(max_retries + 1):
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)  # Let SPA frameworks render
            return True
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"Navigation attempt {attempt + 1} failed: {e}, retrying...")
                await asyncio.sleep(2)
            else:
                logger.error(f"Navigation failed after {max_retries + 1} attempts: {e}")
                return False
    return False


def validate_form_snapshot(tree, form_summary, platform: str) -> dict:
    """Validate that a form snapshot contains expected elements.

    Returns a report dict with pass/fail and details.
    """
    report = {
        "platform": platform,
        "has_a11y_tree": tree is not None,
        "a11y_tree_size": len(_format_a11y_tree(tree)) if tree else 0,
        "form_summary_count": len(form_summary) if form_summary else 0,
        "has_text_inputs": False,
        "has_submit_button": False,
        "has_file_input": False,
        "field_details": [],
        "issues": [],
    }

    if not form_summary and not tree:
        report["issues"].append("Both a11y tree and form_summary are empty")
        return report

    if form_summary:
        for elem in form_summary:
            tag = elem.get("tag", "")
            input_type = elem.get("type", "")
            field_info = {
                "tag": tag,
                "type": input_type,
                "id": elem.get("id", ""),
                "name": elem.get("name", ""),
                "label": elem.get("label", "")[:80],
                "placeholder": elem.get("placeholder", ""),
                "aria-label": elem.get("aria-label", ""),
                "required": elem.get("required", False),
            }
            report["field_details"].append(field_info)

            if tag in ("input", "textarea") and input_type not in ("submit", "button", "hidden"):
                report["has_text_inputs"] = True
            if input_type == "file":
                report["has_file_input"] = True
            if (tag == "button" and "submit" in str(elem).lower()) or input_type == "submit":
                report["has_submit_button"] = True

    # Check a11y tree for interactive elements
    if tree:
        a11y_text = _format_a11y_tree(tree)
        if "textbox" in a11y_text.lower():
            report["has_text_inputs"] = True
        if "button" in a11y_text.lower() and "submit" in a11y_text.lower():
            report["has_submit_button"] = True

    # Validation checks
    if not report["has_text_inputs"]:
        report["issues"].append("No text inputs found")
    if report["form_summary_count"] == 0:
        report["issues"].append("form_summary is empty (JS evaluation returned no elements)")

    return report


def validate_field_analysis(analysis: dict, expected_fields: list, platform: str) -> dict:
    """Validate that Claude CLI field analysis detected expected field types.

    Returns a report dict with pass/fail and field-by-field details.
    """
    report = {
        "platform": platform,
        "page_type": analysis.get("page_type", "unknown") if analysis else "failed",
        "detected_fields": [],
        "missing_fields": [],
        "has_navigation": False,
        "issues": [],
    }

    if not analysis:
        report["issues"].append("Field analysis returned None (Claude CLI failed)")
        report["missing_fields"] = expected_fields
        return report

    fields = analysis.get("fields", [])
    nav = analysis.get("navigation", {})

    # Catalog detected field purposes
    detected_purposes = set()
    for field in fields:
        purpose = field.get("field_purpose", "custom")
        detected_purposes.add(purpose)
        report["detected_fields"].append({
            "name": field.get("name", "?"),
            "purpose": purpose,
            "role": field.get("role", "?"),
            "selector": field.get("selector", ""),
            "required": field.get("required", False),
        })

    # Check for expected fields (use relaxed matching — "first_name" matches both
    # "first_name" and forms that combine into a single "name" field)
    for expected in expected_fields:
        if expected == "first_name" and ("first_name" in detected_purposes or any(
            "name" in f.get("name", "").lower() for f in fields
        )):
            continue
        if expected not in detected_purposes:
            # Check if a related field exists (e.g., "name" covers "first_name")
            found = False
            for field in fields:
                field_name = field.get("name", "").lower()
                if expected.replace("_", " ") in field_name or expected.replace("_", "") in field_name:
                    found = True
                    break
            if not found:
                report["missing_fields"].append(expected)

    # Navigation
    report["has_navigation"] = nav.get("has_submit", False) or nav.get("has_next", False)

    # Issues
    if report["page_type"] != "form":
        report["issues"].append(f"page_type is '{report['page_type']}', expected 'form'")
    if len(fields) == 0:
        report["issues"].append("No fields detected")
    if not report["has_navigation"]:
        report["issues"].append("No submit/next button detected")
    if report["missing_fields"]:
        report["issues"].append(f"Missing expected fields: {report['missing_fields']}")

    return report


# ──────────────────────────────────────────────────────────────
# Phase 1: Form snapshot tests (no Claude CLI needed)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFormSnapshot:
    """Test that get_form_snapshot() captures forms across ATS platforms."""

    async def test_greenhouse_snapshot(self, page):
        """Greenhouse forms should yield rich a11y tree + form_summary."""
        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        tree, form_summary = await get_form_snapshot(page)
        report = validate_form_snapshot(tree, form_summary, "Greenhouse")

        print(f"\n  Greenhouse snapshot report:")
        print(f"    a11y tree: {'YES' if report['has_a11y_tree'] else 'NO'} ({report['a11y_tree_size']} chars)")
        print(f"    form elements: {report['form_summary_count']}")
        print(f"    text inputs: {report['has_text_inputs']}")
        print(f"    file input: {report['has_file_input']}")
        print(f"    submit button: {report['has_submit_button']}")

        if report["issues"]:
            print(f"    issues: {report['issues']}")

        # Core assertions
        assert report["has_text_inputs"], "Greenhouse form should have text inputs"
        assert report["form_summary_count"] > 0, "form_summary should not be empty"

    async def test_lever_snapshot(self, page):
        """Lever forms should yield rich a11y tree + form_summary."""
        url = ATS_TEST_URLS["lever"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Lever form")

        tree, form_summary = await get_form_snapshot(page)
        report = validate_form_snapshot(tree, form_summary, "Lever")

        print(f"\n  Lever snapshot report:")
        print(f"    a11y tree: {'YES' if report['has_a11y_tree'] else 'NO'} ({report['a11y_tree_size']} chars)")
        print(f"    form elements: {report['form_summary_count']}")
        print(f"    text inputs: {report['has_text_inputs']}")
        print(f"    file input: {report['has_file_input']}")
        print(f"    submit button: {report['has_submit_button']}")

        if report["issues"]:
            print(f"    issues: {report['issues']}")

        assert report["has_text_inputs"], "Lever form should have text inputs"
        assert report["form_summary_count"] > 0, "form_summary should not be empty"

    async def test_snapshot_graceful_timeout(self, page):
        """Form snapshot should not crash even on slow/broken pages."""
        # Navigate to a page that exists but might be slow
        url = "https://httpbin.org/delay/2"
        try:
            await page.goto(url, timeout=15000)
        except Exception:
            pass

        # Should not raise — returns (None, []) at worst
        tree, form_summary = await get_form_snapshot(page)
        assert isinstance(form_summary, list), "form_summary should always be a list"

    async def test_format_a11y_tree_real(self, page):
        """Test a11y tree formatting on a real page."""
        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        tree, _ = await get_form_snapshot(page)
        if not tree:
            pytest.skip("No a11y tree available")

        formatted = _format_a11y_tree(tree)
        assert len(formatted) > 100, "Formatted a11y tree should be substantial"
        # Should contain interactive elements
        has_interactive = any(
            kw in formatted.lower()
            for kw in ["textbox", "button", "combobox", "checkbox"]
        )
        assert has_interactive, "A11y tree should contain interactive form elements"

    async def test_format_form_summary_real(self, page):
        """Test form_summary formatting on a real page."""
        url = ATS_TEST_URLS["lever"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Lever form")

        _, form_summary = await get_form_snapshot(page)
        if not form_summary:
            pytest.skip("No form_summary available")

        formatted = _format_form_summary(form_summary)
        assert len(formatted) > 50, "Formatted form_summary should be substantial"
        # Should contain field indices
        assert "[0]" in formatted, "Form summary should have indexed entries"

    async def test_selector_generation_from_real_elements(self, page):
        """Test that build_selector generates working selectors from real form elements."""
        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        _, form_summary = await get_form_snapshot(page)
        if not form_summary:
            pytest.skip("No form_summary available")

        # Generate selectors for each form element and verify they resolve
        selectors_tested = 0
        selectors_found = 0

        for elem in form_summary:
            selector = build_selector(elem)
            if not selector or selector.startswith("xpath:"):
                continue  # Skip xpath for now

            selectors_tested += 1
            try:
                el = await page.wait_for_selector(selector, timeout=2000)
                if el:
                    selectors_found += 1
            except Exception:
                pass

        print(f"\n  Selector test: {selectors_found}/{selectors_tested} selectors resolved")
        if selectors_tested > 0:
            hit_rate = selectors_found / selectors_tested
            assert hit_rate >= 0.5, f"Selector hit rate {hit_rate:.0%} is too low (expected >= 50%)"


# ──────────────────────────────────────────────────────────────
# Phase 2: Field analysis tests (requires Claude CLI)
# ──────────────────────────────────────────────────────────────

def claude_cli_available() -> bool:
    """Check if Claude CLI is available for field analysis tests."""
    import subprocess
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5,
            env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
        )
        return result.returncode == 0
    except Exception:
        return False


# Skip field analysis tests if Claude CLI is not available
requires_claude = pytest.mark.skipif(
    not claude_cli_available(),
    reason="Claude CLI not available — field analysis tests require 'claude' in PATH"
)


@pytest.mark.asyncio
class TestFieldAnalysis:
    """Test that analyze_form_fields() correctly identifies fields via Claude CLI."""

    @requires_claude
    async def test_greenhouse_analysis(self, page):
        """Claude CLI should identify standard Greenhouse fields."""
        from utils.brain import ClaudeBrain
        brain = ClaudeBrain(verbose=False, profile=MOCK_PROFILE)

        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        analysis = await analyze_form_fields(page, brain, url)
        report = validate_field_analysis(
            analysis,
            ATS_TEST_URLS["greenhouse"]["expected_fields"],
            "Greenhouse",
        )

        print(f"\n  Greenhouse field analysis:")
        print(f"    page_type: {report['page_type']}")
        print(f"    fields detected: {len(report['detected_fields'])}")
        for f in report["detected_fields"]:
            print(f"      - {f['purpose']:15s} | {f['name']:25s} | {f['selector'][:40]}")
        print(f"    navigation: {report['has_navigation']}")
        if report["missing_fields"]:
            print(f"    MISSING: {report['missing_fields']}")
        if report["issues"]:
            print(f"    issues: {report['issues']}")

        assert report["page_type"] == "form", "Should detect as form page"
        assert len(report["detected_fields"]) >= 3, "Should detect at least 3 fields"

    @requires_claude
    async def test_lever_analysis(self, page):
        """Claude CLI should identify standard Lever fields."""
        from utils.brain import ClaudeBrain
        brain = ClaudeBrain(verbose=False, profile=MOCK_PROFILE)

        url = ATS_TEST_URLS["lever"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Lever form")

        analysis = await analyze_form_fields(page, brain, url)
        report = validate_field_analysis(
            analysis,
            ATS_TEST_URLS["lever"]["expected_fields"],
            "Lever",
        )

        print(f"\n  Lever field analysis:")
        print(f"    page_type: {report['page_type']}")
        print(f"    fields detected: {len(report['detected_fields'])}")
        for f in report["detected_fields"]:
            print(f"      - {f['purpose']:15s} | {f['name']:25s} | {f['selector'][:40]}")
        print(f"    navigation: {report['has_navigation']}")
        if report["missing_fields"]:
            print(f"    MISSING: {report['missing_fields']}")
        if report["issues"]:
            print(f"    issues: {report['issues']}")

        assert report["page_type"] == "form", "Should detect as form page"
        assert len(report["detected_fields"]) >= 3, "Should detect at least 3 fields"

    @requires_claude
    async def test_field_value_mapping(self, page):
        """Verify that detected fields map to correct profile values."""
        from utils.brain import ClaudeBrain
        brain = ClaudeBrain(verbose=False, profile=MOCK_PROFILE)

        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        analysis = await analyze_form_fields(page, brain, url)
        if not analysis:
            pytest.skip("Field analysis failed")

        # Map detected fields to values
        value_checks = {
            "first_name": "Test",
            "last_name": "Applicant",
            "email": "test@example.com",
            "phone": "555-123-4567",
        }

        for field in analysis.get("fields", []):
            purpose = field.get("field_purpose", "custom")
            if purpose in value_checks:
                value = get_field_value(field, MOCK_PROFILE)
                expected = value_checks[purpose]
                assert value == expected, f"Field {purpose}: got '{value}', expected '{expected}'"
                print(f"    {purpose}: '{value}' OK")


# ──────────────────────────────────────────────────────────────
# Phase 3: iframe detection tests
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestIframeDetection:
    """Test iframe form detection for ATS platforms that use iframes."""

    async def test_no_iframe_on_greenhouse(self, page):
        """Greenhouse does NOT use iframes — should return page, False."""
        url = ATS_TEST_URLS["greenhouse"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Greenhouse form")

        frame, is_iframe = await _find_form_in_iframes(page)
        assert not is_iframe, "Greenhouse should not have forms in iframes"
        assert frame == page, "Should return the main page"

    async def test_no_iframe_on_lever(self, page):
        """Lever does NOT use iframes — should return page, False."""
        url = ATS_TEST_URLS["lever"]["apply_url"]
        nav_ok = await navigate_to_apply(page, url)
        if not nav_ok:
            pytest.skip("Could not navigate to Lever form")

        frame, is_iframe = await _find_form_in_iframes(page)
        assert not is_iframe, "Lever should not have forms in iframes"


# ──────────────────────────────────────────────────────────────
# Standalone report generator
# ──────────────────────────────────────────────────────────────

async def generate_report():
    """Run all snapshot tests and generate a detailed report."""
    from playwright.async_api import async_playwright

    print("=" * 70)
    print("  MR.Jobs Form Adapter — Real-Form Compatibility Report")
    print(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        for platform_key, config in ATS_TEST_URLS.items():
            url = config.get("apply_url") or config["url"]
            platform_name = config["platform"]

            print(f"\n{'─' * 50}")
            print(f"  Testing: {platform_name}")
            print(f"  URL: {url}")
            print(f"{'─' * 50}")

            page = await context.new_page()

            try:
                # Navigate
                nav_ok = await navigate_to_apply(page, url)
                if not nav_ok:
                    results[platform_key] = {
                        "status": "NAVIGATION_FAILED",
                        "platform": platform_name,
                    }
                    print(f"  FAILED: Could not load page")
                    await page.close()
                    continue

                # Take snapshot
                tree, form_summary = await get_form_snapshot(page)
                snapshot_report = validate_form_snapshot(tree, form_summary, platform_name)

                print(f"  A11y tree: {'YES' if snapshot_report['has_a11y_tree'] else 'NO'} "
                      f"({snapshot_report['a11y_tree_size']} chars)")
                print(f"  Form elements: {snapshot_report['form_summary_count']}")
                print(f"  Text inputs: {'YES' if snapshot_report['has_text_inputs'] else 'NO'}")
                print(f"  File input: {'YES' if snapshot_report['has_file_input'] else 'NO'}")
                print(f"  Submit button: {'YES' if snapshot_report['has_submit_button'] else 'NO'}")

                # Print detected fields
                if snapshot_report["field_details"]:
                    print(f"\n  Fields detected:")
                    for f in snapshot_report["field_details"][:15]:
                        label = f["label"] or f["placeholder"] or f["aria-label"] or f["name"] or f["id"]
                        print(f"    <{f['tag']} type=\"{f['type']}\"> "
                              f"{'*' if f['required'] else ' '} {label[:50]}")

                # Try field analysis if Claude CLI is available
                analysis_report = None
                if claude_cli_available():
                    print(f"\n  Running Claude CLI field analysis...")
                    try:
                        from utils.brain import ClaudeBrain
                        brain = ClaudeBrain(verbose=False, profile=MOCK_PROFILE)
                        analysis = await analyze_form_fields(page, brain, url)
                        analysis_report = validate_field_analysis(
                            analysis, config["expected_fields"], platform_name
                        )

                        print(f"  Page type: {analysis_report['page_type']}")
                        print(f"  Fields identified: {len(analysis_report['detected_fields'])}")
                        for f in analysis_report["detected_fields"]:
                            print(f"    {f['purpose']:15s} | {f['name']:25s} | {f['role']}")
                        if analysis_report["missing_fields"]:
                            print(f"  MISSING: {analysis_report['missing_fields']}")
                    except Exception as e:
                        print(f"  Claude CLI analysis failed: {e}")

                # iframe test
                frame, is_iframe = await _find_form_in_iframes(page)
                print(f"\n  Iframe form: {'YES' if is_iframe else 'NO'}")

                if snapshot_report["issues"]:
                    print(f"\n  Issues: {snapshot_report['issues']}")

                results[platform_key] = {
                    "status": "OK" if not snapshot_report["issues"] else "ISSUES",
                    "platform": platform_name,
                    "snapshot": snapshot_report,
                    "analysis": analysis_report,
                    "has_iframe": is_iframe,
                }

            except Exception as e:
                results[platform_key] = {
                    "status": "ERROR",
                    "platform": platform_name,
                    "error": str(e),
                }
                print(f"  ERROR: {e}")

            finally:
                await page.close()

        await browser.close()

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    total = len(results)
    ok = sum(1 for r in results.values() if r["status"] == "OK")
    issues = sum(1 for r in results.values() if r["status"] == "ISSUES")
    failed = sum(1 for r in results.values() if r["status"] in ("NAVIGATION_FAILED", "ERROR"))

    for key, result in results.items():
        status_icon = {"OK": "PASS", "ISSUES": "WARN", "NAVIGATION_FAILED": "FAIL", "ERROR": "FAIL"}.get(
            result["status"], "?"
        )
        print(f"  [{status_icon}] {result['platform']}: {result['status']}")

    print(f"\n  Total: {total} | Pass: {ok} | Warn: {issues} | Fail: {failed}")
    print(f"{'=' * 70}")

    # Save report
    report_dir = Path(".cache/form_reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # Make report JSON-serializable
    serializable_results = {}
    for key, result in results.items():
        serializable_results[key] = {
            "status": result.get("status"),
            "platform": result.get("platform"),
            "has_iframe": result.get("has_iframe"),
        }
        if "snapshot" in result and result["snapshot"]:
            serializable_results[key]["snapshot"] = {
                "has_a11y_tree": result["snapshot"]["has_a11y_tree"],
                "a11y_tree_size": result["snapshot"]["a11y_tree_size"],
                "form_summary_count": result["snapshot"]["form_summary_count"],
                "has_text_inputs": result["snapshot"]["has_text_inputs"],
                "has_submit_button": result["snapshot"]["has_submit_button"],
                "has_file_input": result["snapshot"]["has_file_input"],
                "issues": result["snapshot"]["issues"],
            }
        if "error" in result:
            serializable_results[key]["error"] = result["error"]

    with open(report_path, "w") as f:
        json.dump({
            "generated": datetime.now(timezone.utc).isoformat(),
            "platforms_tested": total,
            "passed": ok,
            "results": serializable_results,
        }, f, indent=2)

    print(f"\n  Report saved: {report_path}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-form adapter tests")
    parser.add_argument("--report", action="store_true", help="Generate compatibility report")
    args = parser.parse_args()

    if args.report:
        asyncio.run(generate_report())
    else:
        print("Usage:")
        print("  pytest tests/test_real_forms.py -v --timeout=120")
        print("  python tests/test_real_forms.py --report")
