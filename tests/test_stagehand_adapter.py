"""
Tests for the CLI-powered Stagehand adapter and smart router.

Tests cover:
1. Accessibility tree parsing and formatting
2. Field purpose detection and value mapping
3. Selector generation strategy (priority order)
4. Cache save/load (action-level and domain-level)
5. Confirmation detection
6. Smart router selection logic
7. Fallback chain (CLI adapter -> Generic)
8. Form snapshot formatting
9. Navigation handling
"""

import os
import sys
import json
import asyncio
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.stagehand_adapter import (
    _cache_key,
    _load_cached_action,
    _save_cached_action,
    _load_domain_cache,
    _save_domain_cache,
    _domain_cache_path,
    _is_confirmation,
    _format_a11y_tree,
    _format_form_summary,
    _fill_via_label,
    _fill_via_placeholder,
    _fill_via_form_summary,
    _fill_via_cli_retry,
    _fill_field_resilient,
    _vision_fallback,
    _detect_and_handle_errors,
    _scroll_to_find_field,
    _find_form_in_iframes,
    _verify_and_retry,
    _verify_fields,
    build_selector,
    build_selector_from_analysis,
    get_field_value,
    is_stagehand_available,
    apply_smart,
    apply_stagehand,
    get_form_snapshot,
    analyze_form_fields,
    CACHE_DIR,
    FIELD_PURPOSE_MAP,
    CONFIRMATION_INDICATORS,
)


# ──────────────────────────────────────────────────────────────
# Test fixtures
# ──────────────────────────────────────────────────────────────

MOCK_PROFILE = {
    "personal": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+1-555-0123",
        "location": "San Francisco, CA",
        "linkedin": "https://linkedin.com/in/janedoe",
        "github": "https://github.com/janedoe",
        "portfolio": "https://janedoe.dev",
    },
    "resume_path": "/tmp/test_resume.pdf",
    "common_answers": {
        "authorized_to_work": "Yes",
        "require_sponsorship": "No",
        "years_experience": "5",
        "salary_expectation": "150000",
        "how_did_you_hear": "Job board",
    },
    "preferences": {
        "roles": ["Software Engineer", "Backend Engineer"],
        "min_match_score": 65,
    },
}

MOCK_BRAIN = MagicMock()
MOCK_BRAIN.answer_question = MagicMock(return_value="Professional answer")
MOCK_BRAIN.ask = MagicMock(return_value="AI response")
MOCK_BRAIN.ask_json = MagicMock(return_value={"status": "done"})


MOCK_A11Y_TREE = {
    "role": "WebArea",
    "name": "Apply - Software Engineer",
    "children": [
        {
            "role": "heading",
            "name": "Apply for Software Engineer",
        },
        {
            "role": "textbox",
            "name": "First Name",
            "required": True,
        },
        {
            "role": "textbox",
            "name": "Last Name",
            "required": True,
        },
        {
            "role": "textbox",
            "name": "Email",
            "required": True,
        },
        {
            "role": "textbox",
            "name": "Phone",
        },
        {
            "role": "combobox",
            "name": "Location",
            "expanded": False,
        },
        {
            "role": "button",
            "name": "Submit Application",
        },
    ],
}


MOCK_FORM_SUMMARY = [
    {
        "tag": "input",
        "type": "text",
        "name": "first_name",
        "id": "first_name",
        "placeholder": "First Name",
        "aria-label": "First Name",
        "role": "",
        "value": "",
        "required": True,
        "label": "First Name",
        "xpath": '//*[@id="first_name"]',
        "visible": True,
        "options": [],
        "index": 0,
    },
    {
        "tag": "input",
        "type": "text",
        "name": "last_name",
        "id": "last_name",
        "placeholder": "Last Name",
        "aria-label": "Last Name",
        "role": "",
        "value": "",
        "required": True,
        "label": "Last Name",
        "xpath": '//*[@id="last_name"]',
        "visible": True,
        "options": [],
        "index": 1,
    },
    {
        "tag": "input",
        "type": "email",
        "name": "email",
        "id": "email",
        "placeholder": "Email Address",
        "aria-label": "Email",
        "role": "",
        "value": "",
        "required": True,
        "label": "Email Address",
        "xpath": '//*[@id="email"]',
        "visible": True,
        "options": [],
        "index": 2,
    },
    {
        "tag": "input",
        "type": "tel",
        "name": "phone",
        "id": "phone_number",
        "placeholder": "Phone Number",
        "aria-label": "",
        "role": "",
        "value": "",
        "required": False,
        "label": "Phone Number",
        "xpath": '//*[@id="phone_number"]',
        "visible": True,
        "options": [],
        "index": 3,
    },
    {
        "tag": "input",
        "type": "file",
        "name": "resume",
        "id": "resume_upload",
        "placeholder": "",
        "aria-label": "Upload Resume",
        "role": "",
        "value": "",
        "required": True,
        "label": "Resume",
        "xpath": '//*[@id="resume_upload"]',
        "visible": True,
        "options": [],
        "index": 4,
    },
    {
        "tag": "select",
        "type": "",
        "name": "location",
        "id": "location_select",
        "placeholder": "",
        "aria-label": "Preferred Location",
        "role": "",
        "value": "",
        "required": False,
        "label": "Preferred Location",
        "xpath": '//*[@id="location_select"]',
        "visible": True,
        "options": [
            {"value": "", "text": "Select..."},
            {"value": "sf", "text": "San Francisco"},
            {"value": "nyc", "text": "New York"},
            {"value": "remote", "text": "Remote"},
        ],
        "index": 5,
    },
]


MOCK_FORM_ANALYSIS = {
    "page_type": "form",
    "fields": [
        {
            "role": "textbox",
            "name": "First Name",
            "field_purpose": "first_name",
            "aria_label": "First Name",
            "placeholder": "First Name",
            "required": True,
            "current_value": "",
            "options": [],
            "custom_question": "",
            "selector": "#first_name",
            "element_index": 0,
        },
        {
            "role": "textbox",
            "name": "Last Name",
            "field_purpose": "last_name",
            "aria_label": "Last Name",
            "placeholder": "Last Name",
            "required": True,
            "current_value": "",
            "options": [],
            "custom_question": "",
            "selector": "#last_name",
            "element_index": 1,
        },
        {
            "role": "textbox",
            "name": "Email",
            "field_purpose": "email",
            "aria_label": "Email",
            "placeholder": "Email Address",
            "required": True,
            "current_value": "",
            "options": [],
            "custom_question": "",
            "selector": "#email",
            "element_index": 2,
        },
        {
            "role": "textbox",
            "name": "Phone",
            "field_purpose": "phone",
            "aria_label": "",
            "placeholder": "Phone Number",
            "required": False,
            "current_value": "",
            "options": [],
            "custom_question": "",
            "selector": "#phone_number",
            "element_index": 3,
        },
        {
            "role": "file_upload",
            "name": "Resume",
            "field_purpose": "resume",
            "aria_label": "Upload Resume",
            "placeholder": "",
            "required": True,
            "current_value": "",
            "options": [],
            "custom_question": "",
            "selector": "#resume_upload",
            "element_index": 4,
        },
    ],
    "navigation": {
        "has_next": False,
        "has_submit": True,
        "next_button_text": "",
        "submit_button_text": "Submit Application",
        "next_button_selector": "",
        "submit_button_selector": "button[type='submit']",
    },
}


# ──────────────────────────────────────────────────────────────
# Unit Tests: Cache key generation
# ──────────────────────────────────────────────────────────────

def test_cache_key_stability():
    """Same URL + action should produce same key."""
    k1 = _cache_key("https://boards.greenhouse.io/stripe/jobs/123", "fill_email")
    k2 = _cache_key("https://boards.greenhouse.io/stripe/jobs/123", "fill_email")
    assert k1 == k2


def test_cache_key_different_urls():
    """Different URLs should produce different keys."""
    k1 = _cache_key("https://boards.greenhouse.io/stripe/jobs/123", "fill_email")
    k2 = _cache_key("https://jobs.lever.co/company/abc", "fill_email")
    assert k1 != k2


def test_cache_key_different_actions():
    """Different actions on same URL should produce different keys."""
    k1 = _cache_key("https://example.com/apply", "fill_email")
    k2 = _cache_key("https://example.com/apply", "fill_phone")
    assert k1 != k2


def test_cache_key_safe_characters():
    """Cache key should only contain safe filesystem characters."""
    key = _cache_key("https://example.com/apply?id=123&foo=bar", "fill the 'email' field")
    assert "/" not in key
    assert "?" not in key
    assert "'" not in key


# ──────────────────────────────────────────────────────────────
# Unit Tests: Cache persistence (action-level)
# ──────────────────────────────────────────────────────────────

def test_cache_save_and_load(tmp_path):
    """Cached actions should persist to disk and load back."""
    import adapters.stagehand_adapter as mod
    original_dir = mod.CACHE_DIR
    mod.CACHE_DIR = tmp_path

    try:
        action = {"selector": "#email", "method": "fill", "description": "Email field"}
        _save_cached_action("test_key", action)
        loaded = _load_cached_action("test_key")
        assert loaded == action
    finally:
        mod.CACHE_DIR = original_dir


def test_cache_load_missing():
    """Loading a non-existent cache key should return None."""
    result = _load_cached_action("nonexistent_key_12345")
    assert result is None


def test_cache_save_overwrite(tmp_path):
    """Saving to an existing key should overwrite."""
    import adapters.stagehand_adapter as mod
    original_dir = mod.CACHE_DIR
    mod.CACHE_DIR = tmp_path

    try:
        _save_cached_action("overwrite_key", {"version": 1})
        _save_cached_action("overwrite_key", {"version": 2})
        loaded = _load_cached_action("overwrite_key")
        assert loaded["version"] == 2
    finally:
        mod.CACHE_DIR = original_dir


# ──────────────────────────────────────────────────────────────
# Unit Tests: Domain-level cache
# ──────────────────────────────────────────────────────────────

def test_domain_cache_save_and_load(tmp_path):
    """Domain cache should save and load field mappings."""
    import adapters.stagehand_adapter as mod
    original_dir = mod.CACHE_DIR
    mod.CACHE_DIR = tmp_path

    try:
        url = "https://jobs.lever.co/company/abc"
        mappings = {
            "first_name": {"selector": "#first-name", "method": "fill"},
            "email": {"selector": '[name="email"]', "method": "fill"},
            "resume": {"selector": 'input[type="file"]', "method": "upload"},
        }
        _save_domain_cache(url, mappings)
        loaded = _load_domain_cache(url)
        assert loaded is not None
        assert loaded["domain"] == "jobs.lever.co"
        assert loaded["field_mappings"] == mappings
        assert "last_updated" in loaded
    finally:
        mod.CACHE_DIR = original_dir


def test_domain_cache_missing():
    """Loading a non-existent domain cache should return None."""
    result = _load_domain_cache("https://nonexistent-domain-12345.com/apply")
    assert result is None


def test_domain_cache_path_format():
    """Domain cache path should use the domain name."""
    path = _domain_cache_path("https://jobs.lever.co/company/abc")
    assert "jobs_lever_co" in path.name
    assert path.suffix == ".json"


# ──────────────────────────────────────────────────────────────
# Unit Tests: Confirmation detection
# ──────────────────────────────────────────────────────────────

def test_confirmation_thank_you():
    assert _is_confirmation("Thank you for your application!") is True


def test_confirmation_submitted():
    assert _is_confirmation("Your application has been successfully submitted.") is True


def test_confirmation_received():
    assert _is_confirmation("We received your application and will review it shortly.") is True


def test_confirmation_complete():
    assert _is_confirmation("Application complete! We'll be in touch.") is True


def test_confirmation_applied():
    assert _is_confirmation("Thanks for applying to our position!") is True


def test_not_confirmation_form_page():
    assert _is_confirmation("Please fill in all required fields.") is False


def test_not_confirmation_empty():
    assert _is_confirmation("") is False


def test_not_confirmation_none():
    assert _is_confirmation(None) is False


# ──────────────────────────────────────────────────────────────
# Unit Tests: Accessibility tree formatting
# ──────────────────────────────────────────────────────────────

def test_format_a11y_tree_basic():
    """Accessibility tree should format into readable text."""
    tree = {
        "role": "textbox",
        "name": "Email Address",
        "required": True,
    }
    result = _format_a11y_tree(tree)
    assert "[textbox]" in result
    assert '"Email Address"' in result
    assert "required=True" in result


def test_format_a11y_tree_nested():
    """Nested accessibility tree should indent children."""
    tree = {
        "role": "WebArea",
        "name": "Page",
        "children": [
            {
                "role": "heading",
                "name": "Apply Now",
            },
            {
                "role": "textbox",
                "name": "Name",
            },
        ],
    }
    result = _format_a11y_tree(tree)
    assert "[WebArea]" in result
    assert "[heading]" in result
    assert "[textbox]" in result
    # Children should be indented
    lines = result.split("\n")
    assert len(lines) >= 3


def test_format_a11y_tree_with_value():
    """Fields with values should show them."""
    tree = {
        "role": "textbox",
        "name": "Email",
        "value": "test@example.com",
    }
    result = _format_a11y_tree(tree)
    assert "value=test@example.com" in result


def test_format_a11y_tree_empty():
    """Empty/None tree should return empty string."""
    assert _format_a11y_tree(None) == ""
    assert _format_a11y_tree({}) == ""


def test_format_a11y_tree_max_depth():
    """Should respect max_depth to prevent infinite recursion."""
    # Build a deeply nested tree
    tree = {"role": "root", "name": "root"}
    current = tree
    for i in range(20):
        child = {"role": f"level_{i}", "name": f"l{i}"}
        current["children"] = [child]
        current = child

    result = _format_a11y_tree(tree, max_depth=3)
    assert "level_0" in result
    assert "level_2" in result
    # Should not go beyond max_depth
    assert "level_10" not in result


# ──────────────────────────────────────────────────────────────
# Unit Tests: Form summary formatting
# ──────────────────────────────────────────────────────────────

def test_format_form_summary():
    """Form summary should format into readable element descriptions."""
    result = _format_form_summary(MOCK_FORM_SUMMARY)
    assert "<input" in result
    assert 'id="first_name"' in result
    assert 'id="email"' in result
    assert "required" in result
    assert "<select" in result
    assert "San Francisco" in result  # option text


def test_format_form_summary_empty():
    """Empty summary should return empty string."""
    assert _format_form_summary([]) == ""


def test_format_form_summary_minimal_field():
    """Should handle fields with minimal attributes."""
    summary = [{"tag": "input", "type": "text", "name": "", "id": "",
                "placeholder": "", "aria-label": "", "role": "", "value": "",
                "required": False, "label": "", "xpath": "", "visible": True,
                "options": [], "index": 0}]
    result = _format_form_summary(summary)
    assert "[0]" in result
    assert "<input" in result


# ──────────────────────────────────────────────────────────────
# Unit Tests: Selector generation strategy
# ──────────────────────────────────────────────────────────────

def test_selector_priority_id():
    """ID selector should be highest priority."""
    field = {"tag": "input", "id": "email", "name": "email_field",
             "aria-label": "Email", "placeholder": "Enter email"}
    assert build_selector(field) == "#email"


def test_selector_priority_name():
    """Name selector should be second priority (when no id)."""
    field = {"tag": "input", "id": "", "name": "email_field",
             "aria-label": "Email", "placeholder": "Enter email"}
    assert build_selector(field) == 'input[name="email_field"]'


def test_selector_priority_aria_label():
    """Aria-label should be third priority."""
    field = {"tag": "input", "id": "", "name": "",
             "aria-label": "Email Address", "placeholder": "Enter email"}
    assert build_selector(field) == 'input[aria-label="Email Address"]'


def test_selector_priority_placeholder():
    """Placeholder should be fourth priority."""
    field = {"tag": "input", "id": "", "name": "",
             "aria-label": "", "placeholder": "Enter your email"}
    assert build_selector(field) == 'input[placeholder="Enter your email"]'


def test_selector_priority_xpath():
    """XPath should be last resort."""
    field = {"tag": "input", "id": "", "name": "",
             "aria-label": "", "placeholder": "",
             "xpath": '/html/body/form/input[3]'}
    assert build_selector(field) == "xpath:/html/body/form/input[3]"


def test_selector_fallback_tag_type():
    """When nothing else available, use tag + type."""
    field = {"tag": "input", "type": "text", "id": "", "name": "",
             "aria-label": "", "placeholder": ""}
    assert build_selector(field) == 'input[type="text"]'


def test_selector_from_analysis_explicit():
    """If analysis includes a selector, use it directly."""
    analysis = {"selector": "#custom-field", "role": "textbox"}
    assert build_selector_from_analysis(analysis) == "#custom-field"


def test_selector_from_analysis_aria_label():
    """Build from aria_label in analysis."""
    analysis = {"selector": "", "aria_label": "Cover Letter", "role": "textbox"}
    assert build_selector_from_analysis(analysis) == '[aria-label="Cover Letter"]'


def test_selector_from_analysis_placeholder():
    """Build from placeholder in analysis."""
    analysis = {"selector": "", "aria_label": "", "placeholder": "Enter phone",
                "role": "textbox", "name": ""}
    result = build_selector_from_analysis(analysis)
    assert 'placeholder="Enter phone"' in result


# ──────────────────────────────────────────────────────────────
# Unit Tests: Field purpose detection and value mapping
# ──────────────────────────────────────────────────────────────

def test_field_value_first_name():
    """first_name purpose should map to profile personal.first_name."""
    field = {"field_purpose": "first_name"}
    assert get_field_value(field, MOCK_PROFILE) == "Jane"


def test_field_value_last_name():
    field = {"field_purpose": "last_name"}
    assert get_field_value(field, MOCK_PROFILE) == "Doe"


def test_field_value_email():
    field = {"field_purpose": "email"}
    assert get_field_value(field, MOCK_PROFILE) == "jane@example.com"


def test_field_value_phone():
    field = {"field_purpose": "phone"}
    assert get_field_value(field, MOCK_PROFILE) == "+1-555-0123"


def test_field_value_location():
    field = {"field_purpose": "location"}
    assert get_field_value(field, MOCK_PROFILE) == "San Francisco, CA"


def test_field_value_linkedin():
    field = {"field_purpose": "linkedin"}
    assert get_field_value(field, MOCK_PROFILE) == "https://linkedin.com/in/janedoe"


def test_field_value_github():
    field = {"field_purpose": "github"}
    assert get_field_value(field, MOCK_PROFILE) == "https://github.com/janedoe"


def test_field_value_portfolio():
    field = {"field_purpose": "portfolio"}
    assert get_field_value(field, MOCK_PROFILE) == "https://janedoe.dev"


def test_field_value_cover_letter():
    field = {"field_purpose": "cover_letter"}
    assert get_field_value(field, MOCK_PROFILE, "My cover letter") == "My cover letter"


def test_field_value_cover_letter_empty():
    field = {"field_purpose": "cover_letter"}
    assert get_field_value(field, MOCK_PROFILE, "") is None


def test_field_value_resume_returns_none():
    """Resume fields return None (handled separately via file upload)."""
    field = {"field_purpose": "resume"}
    assert get_field_value(field, MOCK_PROFILE) is None


def test_field_value_custom_returns_none():
    """Custom fields return None (handled by answer system)."""
    field = {"field_purpose": "custom"}
    assert get_field_value(field, MOCK_PROFILE) is None


def test_field_purpose_map_covers_all_standard_fields():
    """Ensure all standard field purposes are mapped."""
    expected = {"first_name", "last_name", "full_name", "name", "email", "phone",
                "location", "linkedin", "github", "portfolio", "website", "company"}
    assert set(FIELD_PURPOSE_MAP.keys()) == expected


# ──────────────────────────────────────────────────────────────
# Unit Tests: Availability check
# ──────────────────────────────────────────────────────────────

def test_stagehand_always_available():
    """CLI-powered adapter should always be available (no external deps)."""
    assert is_stagehand_available() is True


def test_stagehand_available_no_env_vars():
    """Should be available even without API keys."""
    with patch.dict(os.environ, {}, clear=True):
        assert is_stagehand_available() is True


# ──────────────────────────────────────────────────────────────
# Integration Tests: Smart router
# ──────────────────────────────────────────────────────────────

def _mock_greenhouse_module():
    """Create a mock adapters.greenhouse module to avoid playwright import."""
    mock_mod = MagicMock()
    mock_mod.apply_greenhouse = AsyncMock(return_value=True)
    return mock_mod


def _mock_generic_module():
    """Create a mock adapters.generic module to avoid playwright import."""
    mock_mod = MagicMock()
    mock_mod.apply_generic = AsyncMock(return_value=True)
    return mock_mod


@pytest.mark.asyncio
async def test_smart_router_greenhouse_url():
    """Greenhouse URLs should route to Greenhouse adapter first."""
    mock_page = AsyncMock()
    mock_gh_mod = _mock_greenhouse_module()

    with patch.dict("sys.modules", {"adapters.greenhouse": mock_gh_mod}):
        mock_gh_mod.apply_greenhouse.return_value = True

        result = await apply_smart(
            mock_page,
            "https://boards.greenhouse.io/stripe/jobs/12345",
            MOCK_PROFILE,
            MOCK_BRAIN,
            dry_run=True,
        )

        assert result is True
        mock_gh_mod.apply_greenhouse.assert_called_once()


@pytest.mark.asyncio
async def test_smart_router_greenhouse_platform_override():
    """Platform override should force Greenhouse adapter."""
    mock_page = AsyncMock()
    mock_gh_mod = _mock_greenhouse_module()

    with patch.dict("sys.modules", {"adapters.greenhouse": mock_gh_mod}):
        mock_gh_mod.apply_greenhouse.return_value = True

        result = await apply_smart(
            mock_page,
            "https://example.com/apply",
            MOCK_PROFILE,
            MOCK_BRAIN,
            platform="greenhouse",
            dry_run=True,
        )

        assert result is True
        mock_gh_mod.apply_greenhouse.assert_called_once()


@pytest.mark.asyncio
async def test_smart_router_non_greenhouse_uses_cli_adapter():
    """Non-Greenhouse URLs should use CLI adapter."""
    mock_page = AsyncMock()

    with patch("adapters.stagehand_adapter.apply_stagehand", new_callable=AsyncMock) as mock_sh:
        mock_sh.return_value = True

        result = await apply_smart(
            mock_page,
            "https://jobs.lever.co/company/abc",
            MOCK_PROFILE,
            MOCK_BRAIN,
            dry_run=True,
        )

        assert result is True
        mock_sh.assert_called_once()


@pytest.mark.asyncio
async def test_smart_router_fallback_to_generic():
    """When CLI adapter fails, should fall through to generic."""
    mock_page = AsyncMock()
    mock_gen_mod = _mock_generic_module()

    with patch("adapters.stagehand_adapter.is_stagehand_available", return_value=False):
        with patch.dict("sys.modules", {"adapters.generic": mock_gen_mod}):
            mock_gen_mod.apply_generic.return_value = True

            result = await apply_smart(
                mock_page,
                "https://jobs.lever.co/company/abc",
                MOCK_PROFILE,
                MOCK_BRAIN,
                dry_run=True,
            )

            assert result is True
            mock_gen_mod.apply_generic.assert_called_once()


@pytest.mark.asyncio
async def test_smart_router_greenhouse_then_cli_then_generic():
    """If Greenhouse fails, try CLI adapter, then Generic."""
    mock_page = AsyncMock()
    mock_gh_mod = _mock_greenhouse_module()
    mock_gen_mod = _mock_generic_module()

    with patch.dict("sys.modules", {
        "adapters.greenhouse": mock_gh_mod,
        "adapters.generic": mock_gen_mod,
    }):
        mock_gh_mod.apply_greenhouse.return_value = False  # Greenhouse fails

        with patch("adapters.stagehand_adapter.apply_stagehand", new_callable=AsyncMock) as mock_sh:
            mock_sh.return_value = False  # CLI adapter fails too
            mock_gen_mod.apply_generic.return_value = True

            result = await apply_smart(
                mock_page,
                "https://boards.greenhouse.io/company/jobs/123",
                MOCK_PROFILE,
                MOCK_BRAIN,
                dry_run=True,
            )

            mock_gh_mod.apply_greenhouse.assert_called_once()
            mock_sh.assert_called_once()
            mock_gen_mod.apply_generic.assert_called_once()
            assert result is True


@pytest.mark.asyncio
async def test_smart_router_greenhouse_exception_fallback():
    """If Greenhouse adapter raises an exception, should fall through."""
    mock_page = AsyncMock()
    mock_gh_mod = _mock_greenhouse_module()

    with patch.dict("sys.modules", {"adapters.greenhouse": mock_gh_mod}):
        mock_gh_mod.apply_greenhouse.side_effect = Exception("Greenhouse crash")

        with patch("adapters.stagehand_adapter.apply_stagehand", new_callable=AsyncMock) as mock_sh:
            mock_sh.return_value = True

            result = await apply_smart(
                mock_page,
                "https://boards.greenhouse.io/company/jobs/123",
                MOCK_PROFILE,
                MOCK_BRAIN,
                dry_run=True,
            )

            assert result is True
            mock_sh.assert_called_once()


# ──────────────────────────────────────────────────────────────
# Integration Tests: apply_stagehand (mocked)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_stagehand_confirmation_page():
    """Should detect a confirmation page and return True."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value="Thank you for your application!")
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.title = AsyncMock(return_value="Application Submitted")

    with patch("adapters.stagehand_adapter._detect_page_state", new_callable=AsyncMock) as mock_detect:
        mock_detect.return_value = "confirmation"

        result = await apply_stagehand(
            mock_page,
            "https://example.com/apply",
            MOCK_PROFILE,
            MOCK_BRAIN,
            dry_run=True,
        )

        assert result is True


@pytest.mark.asyncio
async def test_apply_stagehand_page_load_failure():
    """Should return False if page fails to load."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(side_effect=Exception("Network error"))

    result = await apply_stagehand(
        mock_page,
        "https://example.com/apply",
        MOCK_PROFILE,
        MOCK_BRAIN,
        dry_run=True,
    )

    assert result is False


@pytest.mark.asyncio
async def test_apply_stagehand_form_analysis_with_dry_run():
    """Should fill fields and stop at submit in dry run mode."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.title = AsyncMock(return_value="Apply")
    mock_page.inner_text = AsyncMock(return_value="Apply for Software Engineer")

    mock_brain = MagicMock()
    mock_brain.ask_json = MagicMock(return_value=MOCK_FORM_ANALYSIS)

    # Mock _detect_page_state to return "form"
    # Mock get_form_snapshot to return our test data
    # Mock fill/click operations
    with patch("adapters.stagehand_adapter._detect_page_state", new_callable=AsyncMock) as mock_detect:
        mock_detect.return_value = "form"

        with patch("adapters.stagehand_adapter.get_form_snapshot", new_callable=AsyncMock) as mock_snap:
            mock_snap.return_value = (MOCK_A11Y_TREE, MOCK_FORM_SUMMARY)

            with patch("adapters.stagehand_adapter._fill_form_step", new_callable=AsyncMock) as mock_fill:
                mock_fill.return_value = 4

                with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
                    mock_verify.return_value = []

                    with patch("adapters.stagehand_adapter._handle_navigation_step", new_callable=AsyncMock) as mock_nav:
                        mock_nav.return_value = "dry_run_stop"

                        result = await apply_stagehand(
                            mock_page,
                            "https://example.com/apply",
                            MOCK_PROFILE,
                            mock_brain,
                            dry_run=True,
                        )

                        assert result is True
                        mock_fill.assert_called_once()
                        mock_nav.assert_called_once()


@pytest.mark.asyncio
async def test_apply_stagehand_multi_step_wizard():
    """Should handle multi-step forms by looping through steps."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.title = AsyncMock(return_value="Apply")

    step_count = 0

    async def mock_detect_state(page):
        nonlocal step_count
        step_count += 1
        return "form"

    nav_calls = [0]

    async def mock_nav(page, analysis, dry_run):
        nav_calls[0] += 1
        if nav_calls[0] < 3:
            return "next"
        return "done"

    mock_brain = MagicMock()
    mock_brain.ask_json = MagicMock(return_value=MOCK_FORM_ANALYSIS)

    with patch("adapters.stagehand_adapter._detect_page_state", side_effect=mock_detect_state):
        with patch("adapters.stagehand_adapter.get_form_snapshot", new_callable=AsyncMock) as mock_snap:
            mock_snap.return_value = (MOCK_A11Y_TREE, MOCK_FORM_SUMMARY)
            with patch("adapters.stagehand_adapter._fill_form_step", new_callable=AsyncMock) as mock_fill:
                mock_fill.return_value = 3
                with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
                    mock_verify.return_value = []
                    with patch("adapters.stagehand_adapter._handle_navigation_step", side_effect=mock_nav):
                        result = await apply_stagehand(
                            mock_page,
                            "https://example.com/apply",
                            MOCK_PROFILE,
                            mock_brain,
                            dry_run=False,
                        )

                        assert result is True
                        assert nav_calls[0] == 3  # navigated through 3 steps


@pytest.mark.asyncio
async def test_apply_stagehand_max_steps():
    """Should give up after max_steps."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.title = AsyncMock(return_value="Apply")

    mock_brain = MagicMock()
    mock_brain.ask_json = MagicMock(return_value=MOCK_FORM_ANALYSIS)

    with patch("adapters.stagehand_adapter._detect_page_state", new_callable=AsyncMock) as mock_detect:
        mock_detect.return_value = "form"
        with patch("adapters.stagehand_adapter.get_form_snapshot", new_callable=AsyncMock) as mock_snap:
            mock_snap.return_value = (MOCK_A11Y_TREE, MOCK_FORM_SUMMARY)
            with patch("adapters.stagehand_adapter._fill_form_step", new_callable=AsyncMock) as mock_fill:
                mock_fill.return_value = 0
                with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
                    mock_verify.return_value = []
                    with patch("adapters.stagehand_adapter._handle_navigation_step", new_callable=AsyncMock) as mock_nav:
                        mock_nav.return_value = "next"  # always says "next" so we never finish

                        result = await apply_stagehand(
                            mock_page,
                            "https://example.com/apply",
                            MOCK_PROFILE,
                            mock_brain,
                            dry_run=False,
                            max_steps=3,
                        )

                        assert result is False
                        assert mock_nav.call_count == 3


# ──────────────────────────────────────────────────────────────
# Integration Tests: Form snapshot
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_form_snapshot():
    """Should return accessibility tree and form summary."""
    mock_page = AsyncMock()
    mock_page.accessibility = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=MOCK_A11Y_TREE)
    mock_page.evaluate = AsyncMock(return_value=MOCK_FORM_SUMMARY)

    tree, summary = await get_form_snapshot(mock_page)

    assert tree == MOCK_A11Y_TREE
    assert summary == MOCK_FORM_SUMMARY


@pytest.mark.asyncio
async def test_get_form_snapshot_a11y_failure():
    """Should handle accessibility tree failure gracefully."""
    mock_page = AsyncMock()
    mock_page.accessibility = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(side_effect=Exception("a11y not available"))
    mock_page.evaluate = AsyncMock(return_value=MOCK_FORM_SUMMARY)

    tree, summary = await get_form_snapshot(mock_page)

    assert tree is None
    assert summary == MOCK_FORM_SUMMARY


# ──────────────────────────────────────────────────────────────
# Integration Tests: analyze_form_fields
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_form_fields_success():
    """Should call brain.ask_json with formatted prompt."""
    mock_page = AsyncMock()
    mock_page.accessibility = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=MOCK_A11Y_TREE)
    mock_page.evaluate = AsyncMock(return_value=MOCK_FORM_SUMMARY)
    mock_page.title = AsyncMock(return_value="Apply - Software Engineer")

    mock_brain = MagicMock()
    mock_brain.ask_json = MagicMock(return_value=MOCK_FORM_ANALYSIS)

    result = await analyze_form_fields(mock_page, mock_brain, "https://example.com/apply")

    assert result is not None
    assert result["page_type"] == "form"
    mock_brain.ask_json.assert_called_once()

    # Verify the prompt contains the right sections
    call_args = mock_brain.ask_json.call_args
    prompt = call_args[0][0]
    assert "ACCESSIBILITY TREE:" in prompt
    assert "FORM ELEMENTS:" in prompt
    assert "Apply - Software Engineer" in prompt


@pytest.mark.asyncio
async def test_analyze_form_fields_no_elements():
    """Should return None when no form elements found."""
    mock_page = AsyncMock()
    mock_page.accessibility = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=None)
    mock_page.evaluate = AsyncMock(return_value=[])

    result = await analyze_form_fields(mock_page, MOCK_BRAIN, "https://example.com/apply")

    assert result is None


@pytest.mark.asyncio
async def test_analyze_form_fields_brain_failure():
    """Should return None when brain.ask_json fails."""
    mock_page = AsyncMock()
    mock_page.accessibility = AsyncMock()
    mock_page.accessibility.snapshot = AsyncMock(return_value=MOCK_A11Y_TREE)
    mock_page.evaluate = AsyncMock(return_value=MOCK_FORM_SUMMARY)
    mock_page.title = AsyncMock(return_value="Apply")

    failing_brain = MagicMock()
    failing_brain.ask_json = MagicMock(side_effect=Exception("CLI timeout"))

    result = await analyze_form_fields(mock_page, failing_brain, "https://example.com/apply")

    assert result is None


# ──────────────────────────────────────────────────────────────
# Confirmation indicators completeness
# ──────────────────────────────────────────────────────────────

def test_confirmation_indicators_coverage():
    """All known confirmation phrases should be detected."""
    test_phrases = [
        "Thank you for applying",
        "Application submitted successfully",
        "We received your application",
        "Your application has been received",
        "Successfully submitted your application",
        "You have applied for this position",
        "Application complete",
        "Thanks for applying",
        "We'll review your application",
    ]
    for phrase in test_phrases:
        assert _is_confirmation(phrase), f"Failed to detect: '{phrase}'"


# ──────────────────────────────────────────────────────────────
# Unit Tests: _fill_via_label
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fill_via_label_success():
    """Should find input via label text and fill it."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait_for_selector(sel, timeout=3000):
        if 'has-text("First Name")' in sel and "input" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait_for_selector

    result = await _fill_via_label(mock_page, "First Name", "Jane")
    assert result is True
    mock_el.fill.assert_called_once_with("Jane")


@pytest.mark.asyncio
async def test_fill_via_label_empty_label():
    """Should return False for empty label text."""
    mock_page = AsyncMock()
    result = await _fill_via_label(mock_page, "", "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_label_none_label():
    """Should return False for None label text."""
    mock_page = AsyncMock()
    result = await _fill_via_label(mock_page, None, "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_label_no_match():
    """Should return False when no selector matches."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(return_value=None)

    result = await _fill_via_label(mock_page, "Nonexistent Field", "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_label_fill_exception_falls_to_type():
    """Should fall back to keyboard.type if fill() throws."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock(side_effect=Exception("fill not supported"))
    mock_el.click = AsyncMock()
    mock_page.keyboard = AsyncMock()
    mock_page.keyboard.type = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if 'has-text("Name")' in sel and "input" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    result = await _fill_via_label(mock_page, "Name", "Jane")
    assert result is True
    mock_page.keyboard.type.assert_called_once_with("Jane", delay=10)


# ──────────────────────────────────────────────────────────────
# Unit Tests: _fill_via_placeholder
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fill_via_placeholder_success():
    """Should find input by placeholder and fill it."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if "Enter your email" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    field = {"placeholder": "Enter your email"}
    result = await _fill_via_placeholder(mock_page, field, "test@example.com")
    assert result is True
    mock_el.fill.assert_called_once_with("test@example.com")


@pytest.mark.asyncio
async def test_fill_via_placeholder_empty():
    """Should return False when field has no placeholder."""
    mock_page = AsyncMock()
    field = {"placeholder": ""}
    result = await _fill_via_placeholder(mock_page, field, "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_placeholder_missing_key():
    """Should return False when field has no placeholder key."""
    mock_page = AsyncMock()
    field = {}
    result = await _fill_via_placeholder(mock_page, field, "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_placeholder_no_match():
    """Should return False when no element matches placeholder."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(return_value=None)

    field = {"placeholder": "Nonexistent"}
    result = await _fill_via_placeholder(mock_page, field, "value")
    assert result is False


# ──────────────────────────────────────────────────────────────
# Unit Tests: _fill_via_form_summary
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fill_via_form_summary_match_by_name():
    """Should match field by name against form_summary label."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if "first_name" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    field = {"name": "First Name", "field_purpose": "first_name"}
    result = await _fill_via_form_summary(mock_page, field, MOCK_FORM_SUMMARY, "Jane")
    assert result is True


@pytest.mark.asyncio
async def test_fill_via_form_summary_match_by_purpose():
    """Should match field by purpose against form_summary id."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if "email" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    field = {"name": "Email", "field_purpose": "email"}
    result = await _fill_via_form_summary(mock_page, field, MOCK_FORM_SUMMARY, "test@example.com")
    assert result is True


@pytest.mark.asyncio
async def test_fill_via_form_summary_no_match():
    """Should return False when no form_summary element matches."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(return_value=None)

    field = {"name": "Nonexistent", "field_purpose": "nonexistent"}
    result = await _fill_via_form_summary(mock_page, field, MOCK_FORM_SUMMARY, "value")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_form_summary_empty_summary():
    """Should return False with empty form_summary."""
    mock_page = AsyncMock()
    field = {"name": "First Name", "field_purpose": "first_name"}
    result = await _fill_via_form_summary(mock_page, field, [], "Jane")
    assert result is False


@pytest.mark.asyncio
async def test_fill_via_form_summary_select_element():
    """Should use _select_option for select elements."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if "location_select" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait
    mock_el.select_option = AsyncMock()

    field = {"name": "Preferred Location", "field_purpose": "location"}
    result = await _fill_via_form_summary(mock_page, field, MOCK_FORM_SUMMARY, "San Francisco")
    assert result is True


# ──────────────────────────────────────────────────────────────
# Unit Tests: _detect_and_handle_errors
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_errors_found():
    """Should detect validation errors on the page."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[
        {"text": "Email is required", "nearestInput": {"id": "email", "name": "email", "type": "text"}},
        {"text": "Phone number is invalid", "nearestInput": {"id": "phone", "name": "phone", "type": "tel"}},
    ])

    result = await _detect_and_handle_errors(mock_page, MOCK_BRAIN)
    assert result is True


@pytest.mark.asyncio
async def test_detect_errors_none():
    """Should return False when no errors on page."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=[])

    result = await _detect_and_handle_errors(mock_page, MOCK_BRAIN)
    assert result is False


@pytest.mark.asyncio
async def test_detect_errors_exception():
    """Should return False on evaluate exception."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=Exception("eval failed"))

    result = await _detect_and_handle_errors(mock_page, MOCK_BRAIN)
    assert result is False


# ──────────────────────────────────────────────────────────────
# Unit Tests: _verify_and_retry
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_and_retry_all_pass():
    """Should return 0 when all fields pass verification."""
    mock_page = AsyncMock()

    with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = []

        result = await _verify_and_retry(
            mock_page, MOCK_FORM_ANALYSIS, MOCK_FORM_SUMMARY,
            MOCK_PROFILE, MOCK_BRAIN, "cover letter"
        )
        assert result == 0
        mock_verify.assert_called_once()


@pytest.mark.asyncio
async def test_verify_and_retry_retries_failed():
    """Should retry failed fields and eventually succeed."""
    mock_page = AsyncMock()
    call_count = [0]

    async def mock_verify(page, analysis, profile, cover):
        call_count[0] += 1
        if call_count[0] <= 1:
            return ["First Name"]
        return []

    with patch("adapters.stagehand_adapter._verify_fields", side_effect=mock_verify):
        with patch("adapters.stagehand_adapter._fill_field_resilient", new_callable=AsyncMock) as mock_fill:
            mock_fill.return_value = True

            result = await _verify_and_retry(
                mock_page, MOCK_FORM_ANALYSIS, MOCK_FORM_SUMMARY,
                MOCK_PROFILE, MOCK_BRAIN, "cover letter", max_retries=2
            )
            assert result == 0


@pytest.mark.asyncio
async def test_verify_and_retry_exhausts_retries():
    """Should return count of still-failed fields after max retries."""
    mock_page = AsyncMock()

    with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ["First Name", "Email"]

        with patch("adapters.stagehand_adapter._fill_field_resilient", new_callable=AsyncMock) as mock_fill:
            mock_fill.return_value = False  # All retries fail

            result = await _verify_and_retry(
                mock_page, MOCK_FORM_ANALYSIS, MOCK_FORM_SUMMARY,
                MOCK_PROFILE, MOCK_BRAIN, "cover letter", max_retries=2
            )
            assert result == 2


@pytest.mark.asyncio
async def test_verify_and_retry_zero_retries():
    """With max_retries=0, should not retry at all."""
    mock_page = AsyncMock()

    with patch("adapters.stagehand_adapter._verify_fields", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ["First Name"]

        result = await _verify_and_retry(
            mock_page, MOCK_FORM_ANALYSIS, MOCK_FORM_SUMMARY,
            MOCK_PROFILE, MOCK_BRAIN, "cover letter", max_retries=0
        )
        assert result == 1
        # Only called once (initial check), no retries
        assert mock_verify.call_count == 1


# ──────────────────────────────────────────────────────────────
# Unit Tests: _scroll_to_find_field
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scroll_to_find_field_found_immediately():
    """Should return True when field is found in current viewport."""
    mock_page = AsyncMock()

    eval_results = []
    call_idx = [0]

    async def mock_evaluate(script, *args, **kwargs):
        call_idx[0] += 1
        if "innerHeight" in script:
            return 800
        elif "scrollHeight" in script:
            return 1600
        elif "labels" in script:
            return True  # Found on first check
        elif "scrollTo" in script:
            return None
        return None

    mock_page.evaluate = mock_evaluate

    result = await _scroll_to_find_field(mock_page, "Email")
    assert result is True


@pytest.mark.asyncio
async def test_scroll_to_find_field_not_found():
    """Should return False when field is not found anywhere."""
    mock_page = AsyncMock()

    async def mock_evaluate(script, *args, **kwargs):
        if "innerHeight" in script:
            return 800
        elif "scrollHeight" in script:
            return 800  # Only 1 viewport, no scrolling needed
        elif "labels" in script:
            return False  # Never found
        return None

    mock_page.evaluate = mock_evaluate

    result = await _scroll_to_find_field(mock_page, "Nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_scroll_to_find_field_exception():
    """Should return False on exception."""
    mock_page = AsyncMock()
    mock_page.evaluate = AsyncMock(side_effect=Exception("JS error"))

    result = await _scroll_to_find_field(mock_page, "Email")
    assert result is False


# ──────────────────────────────────────────────────────────────
# Unit Tests: _fill_field_resilient (multi-strategy)
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resilient_fill_first_strategy_succeeds():
    """Should succeed on first strategy and not try others."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        if sel == "#first_name":
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    field = {
        "selector": "#first_name",
        "name": "First Name",
        "field_purpose": "first_name",
        "role": "textbox",
        "placeholder": "",
    }

    result = await _fill_field_resilient(mock_page, field, "Jane", MOCK_FORM_SUMMARY, MOCK_BRAIN)
    assert result is True


@pytest.mark.asyncio
async def test_resilient_fill_falls_to_label_strategy():
    """Should try label strategy when selector strategy fails."""
    mock_page = AsyncMock()
    mock_el = AsyncMock()
    mock_el.fill = AsyncMock()

    async def mock_wait(sel, timeout=3000):
        # Fail on direct selector, succeed on label-based
        if 'has-text("First Name")' in sel and "input" in sel:
            return mock_el
        return None

    mock_page.wait_for_selector = mock_wait

    field = {
        "selector": "#nonexistent",
        "name": "First Name",
        "field_purpose": "first_name",
        "role": "textbox",
        "placeholder": "",
    }

    result = await _fill_field_resilient(mock_page, field, "Jane", [], MOCK_BRAIN)
    assert result is True


@pytest.mark.asyncio
async def test_resilient_fill_all_strategies_fail():
    """Should return False when all strategies fail."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(return_value=None)
    mock_page.evaluate = AsyncMock(return_value=[])

    field = {
        "selector": "#nonexistent",
        "name": "Nonexistent",
        "field_purpose": "nonexistent",
        "role": "textbox",
        "placeholder": "",
    }

    result = await _fill_field_resilient(mock_page, field, "value", [], MOCK_BRAIN)
    assert result is False


@pytest.mark.asyncio
async def test_resilient_fill_exception_does_not_crash():
    """Exceptions in strategies should be caught, not propagated."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(side_effect=Exception("crash"))
    mock_page.evaluate = AsyncMock(side_effect=Exception("eval crash"))

    field = {
        "selector": "#broken",
        "name": "Broken",
        "field_purpose": "broken",
        "role": "textbox",
        "placeholder": "broken",
    }

    # Should not raise — should return False gracefully
    result = await _fill_field_resilient(mock_page, field, "value", [], MOCK_BRAIN)
    assert result is False


# ──────────────────────────────────────────────────────────────
# Unit Tests: _find_form_in_iframes
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_iframe_detection_no_iframes():
    """Should return (page, False) when no iframes have forms."""
    mock_page = AsyncMock()
    mock_page.main_frame = MagicMock()
    mock_page.frames = [mock_page.main_frame]

    frame, is_iframe = await _find_form_in_iframes(mock_page)
    assert frame is mock_page
    assert is_iframe is False


@pytest.mark.asyncio
async def test_iframe_detection_with_form():
    """Should return (frame, True) when iframe contains a form."""
    mock_page = AsyncMock()
    mock_main_frame = MagicMock()
    mock_page.main_frame = mock_main_frame

    mock_iframe = AsyncMock()
    mock_iframe.url = "https://app.workday.com/form"
    mock_iframe.evaluate = AsyncMock(return_value=5)  # 5 form fields

    mock_page.frames = [mock_main_frame, mock_iframe]

    frame, is_iframe = await _find_form_in_iframes(mock_page)
    assert frame is mock_iframe
    assert is_iframe is True


@pytest.mark.asyncio
async def test_iframe_detection_iframe_too_few_fields():
    """Should skip iframes with too few form fields."""
    mock_page = AsyncMock()
    mock_main_frame = MagicMock()
    mock_page.main_frame = mock_main_frame

    mock_iframe = AsyncMock()
    mock_iframe.url = "https://example.com/ads"
    mock_iframe.evaluate = AsyncMock(return_value=1)  # Only 1 field

    mock_page.frames = [mock_main_frame, mock_iframe]

    frame, is_iframe = await _find_form_in_iframes(mock_page)
    assert frame is mock_page
    assert is_iframe is False


@pytest.mark.asyncio
async def test_iframe_detection_exception():
    """Should return (page, False) on exception."""
    mock_page = AsyncMock()
    mock_page.frames = PropertyMock(side_effect=Exception("frames error"))

    frame, is_iframe = await _find_form_in_iframes(mock_page)
    assert frame is mock_page
    assert is_iframe is False


# ──────────────────────────────────────────────────────────────
# Run tests
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
