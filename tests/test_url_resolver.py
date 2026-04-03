"""Tests for utils/url_resolver.py — URL resolution from aggregator to ATS."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from utils.url_resolver import (
    is_ats_url,
    is_aggregator_url,
    _search_company_ats,
    _extract_urls_from_text,
    _extract_email_from_text,
    resolve_apply_url,
    COMPANY_ATS_MAP,
    ATS_DOMAINS,
    AGGREGATOR_DOMAINS,
)


# ─── is_ats_url ─────────────────────────────────────────────────────────────

class TestIsAtsUrl:
    def test_greenhouse_board(self):
        assert is_ats_url("https://boards.greenhouse.io/stripe/jobs/12345")

    def test_greenhouse_job_boards(self):
        assert is_ats_url("https://job-boards.greenhouse.io/anthropic/jobs/4613568008")

    def test_lever(self):
        assert is_ats_url("https://jobs.lever.co/palantir/abc-123")

    def test_ashby(self):
        assert is_ats_url("https://jobs.ashbyhq.com/ramp/abc-123/application")

    def test_workday(self):
        assert is_ats_url("https://company.myworkdayjobs.com/en-US/External/job/12345")

    def test_smartrecruiters(self):
        assert is_ats_url("https://careers.smartrecruiters.com/Company/12345")

    def test_indeed_not_ats(self):
        assert not is_ats_url("https://www.indeed.com/viewjob?jk=abc123")

    def test_linkedin_not_ats(self):
        assert not is_ats_url("https://www.linkedin.com/jobs/view/12345")

    def test_remoteok_not_ats(self):
        assert not is_ats_url("https://remoteok.com/remote-jobs/12345")

    def test_hn_not_ats(self):
        assert not is_ats_url("https://news.ycombinator.com/item?id=12345")

    def test_generic_not_ats(self):
        assert not is_ats_url("https://example.com/careers")

    def test_empty_string(self):
        assert not is_ats_url("")

    def test_invalid_url(self):
        assert not is_ats_url("not-a-url")


# ─── is_aggregator_url ──────────────────────────────────────────────────────

class TestIsAggregatorUrl:
    def test_indeed(self):
        assert is_aggregator_url("https://www.indeed.com/viewjob?jk=abc123")

    def test_linkedin(self):
        assert is_aggregator_url("https://www.linkedin.com/jobs/view/12345")

    def test_glassdoor(self):
        assert is_aggregator_url("https://www.glassdoor.com/job-listing/abc")

    def test_ziprecruiter(self):
        assert is_aggregator_url("https://www.ziprecruiter.com/c/Company/Job/123")

    def test_remoteok(self):
        assert is_aggregator_url("https://remoteok.com/remote-jobs/12345")

    def test_adzuna(self):
        assert is_aggregator_url("https://www.adzuna.com/details/12345")

    def test_hn(self):
        assert is_aggregator_url("https://news.ycombinator.com/item?id=12345")

    def test_greenhouse_not_aggregator(self):
        assert not is_aggregator_url("https://boards.greenhouse.io/stripe/jobs/12345")

    def test_lever_not_aggregator(self):
        assert not is_aggregator_url("https://jobs.lever.co/palantir/abc-123")


# ─── _search_company_ats ────────────────────────────────────────────────────

class TestSearchCompanyAts:
    def test_exact_match(self):
        result = _search_company_ats("anthropic")
        assert result is not None
        assert "greenhouse" in result

    def test_case_insensitive(self):
        result = _search_company_ats("Anthropic")
        assert result is not None

    def test_partial_match(self):
        result = _search_company_ats("Anthropic Inc")
        assert result is not None

    def test_unknown_company(self):
        result = _search_company_ats("TotallyUnknownCompany12345")
        assert result is None

    def test_empty_company(self):
        result = _search_company_ats("")
        assert result is None

    def test_stripe(self):
        result = _search_company_ats("stripe")
        assert result is not None


# ─── _extract_urls_from_text ─────────────────────────────────────────────────

class TestExtractUrlsFromText:
    def test_simple_url(self):
        urls = _extract_urls_from_text("Apply at https://careers.company.com/jobs")
        assert "https://careers.company.com/jobs" in urls

    def test_multiple_urls(self):
        text = "Visit https://example.com or https://other.com for more"
        urls = _extract_urls_from_text(text)
        assert len(urls) == 2

    def test_url_with_trailing_punctuation(self):
        text = "Apply here: https://careers.company.com/jobs."
        urls = _extract_urls_from_text(text)
        assert urls[0] == "https://careers.company.com/jobs"

    def test_no_urls(self):
        urls = _extract_urls_from_text("No URLs in this text at all")
        assert urls == []

    def test_ats_url_in_hn_post(self):
        text = "Acme Corp | Senior Engineer | Remote | https://boards.greenhouse.io/acme/jobs/123"
        urls = _extract_urls_from_text(text)
        assert "https://boards.greenhouse.io/acme/jobs/123" in urls


# ─── _extract_email_from_text ────────────────────────────────────────────────

class TestExtractEmailFromText:
    def test_simple_email(self):
        email = _extract_email_from_text("Apply to jobs@company.com")
        assert email == "jobs@company.com"

    def test_no_email(self):
        email = _extract_email_from_text("No email here, apply at the website")
        assert email is None

    def test_email_in_hn_post(self):
        text = "Acme Corp | Engineer | Remote | Send resume to hiring@acme.io"
        email = _extract_email_from_text(text)
        assert email == "hiring@acme.io"


# ─── resolve_apply_url ──────────────────────────────────────────────────────

class TestResolveApplyUrl:
    @pytest.fixture
    def mock_page(self):
        page = AsyncMock()
        page.url = "https://example.com"
        page.goto = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])
        page.wait_for_selector = AsyncMock(return_value=None)
        return page

    @pytest.mark.asyncio
    async def test_already_ats_url(self, mock_page):
        result = await resolve_apply_url(
            mock_page,
            job_url="https://boards.greenhouse.io/stripe/jobs/12345",
        )
        assert result["resolution"] == "ats_direct"
        assert result["resolved_url"] == "https://boards.greenhouse.io/stripe/jobs/12345"
        # Should not navigate anywhere
        mock_page.goto.assert_not_called()

    @pytest.mark.asyncio
    async def test_company_lookup_ats(self, mock_page):
        result = await resolve_apply_url(
            mock_page,
            job_url="https://www.indeed.com/viewjob?jk=abc123",
            company="Anthropic",
        )
        assert result["resolution"] == "company_lookup"
        assert "greenhouse" in result["resolved_url"]

    @pytest.mark.asyncio
    async def test_hn_url_extraction(self, mock_page):
        hn_desc = "Acme Corp | Senior Engineer | Remote | Apply: https://jobs.lever.co/acme/abc-123"
        result = await resolve_apply_url(
            mock_page,
            job_url="https://news.ycombinator.com/item?id=12345",
            company="Acme Corp",
            description=hn_desc,
            platform="hackernews",
        )
        assert result["resolution"] == "hn_extract"
        assert "lever.co" in result["resolved_url"]

    @pytest.mark.asyncio
    async def test_hn_email_extraction(self, mock_page):
        hn_desc = "Acme Corp | Senior Engineer | Remote | Send resume to hiring@acme.io"
        result = await resolve_apply_url(
            mock_page,
            job_url="https://news.ycombinator.com/item?id=12345",
            description=hn_desc,
            platform="hackernews",
        )
        assert result["apply_email"] == "hiring@acme.io"

    @pytest.mark.asyncio
    async def test_hn_careers_url_extraction(self, mock_page):
        hn_desc = "Acme Corp | Engineer | Remote | https://acme.com/careers"
        result = await resolve_apply_url(
            mock_page,
            job_url="https://news.ycombinator.com/item?id=12345",
            description=hn_desc,
            platform="hackernews",
        )
        assert result["resolution"] == "hn_extract"
        assert "careers" in result["resolved_url"]

    @pytest.mark.asyncio
    async def test_redirect_to_ats(self, mock_page):
        # Simulate: Indeed redirects to Greenhouse
        mock_page.goto = AsyncMock(return_value=MagicMock())
        mock_page.url = "https://boards.greenhouse.io/company/jobs/123"

        result = await resolve_apply_url(
            mock_page,
            job_url="https://www.indeed.com/viewjob?jk=abc123",
            company="UnknownCompany12345",
        )
        assert result["resolution"] == "redirect"
        assert "greenhouse" in result["resolved_url"]

    @pytest.mark.asyncio
    async def test_apply_link_extraction(self, mock_page):
        # First call: redirect to a non-ATS page
        mock_page.goto = AsyncMock(return_value=MagicMock())
        mock_page.url = "https://company.com/job/12345"

        # Simulate finding an apply link on the page
        mock_link = AsyncMock()
        mock_link.get_attribute = AsyncMock(return_value="https://jobs.lever.co/company/abc-123/apply")
        mock_page.wait_for_selector = AsyncMock(return_value=mock_link)

        result = await resolve_apply_url(
            mock_page,
            job_url="https://www.indeed.com/viewjob?jk=abc123",
            company="UnknownCompany12345",
        )
        assert result["resolution"] == "apply_link"
        assert "lever.co" in result["resolved_url"]

    @pytest.mark.asyncio
    async def test_unresolved_aggregator(self, mock_page):
        # Can't resolve — goto fails
        mock_page.goto = AsyncMock(side_effect=Exception("timeout"))

        result = await resolve_apply_url(
            mock_page,
            job_url="https://www.indeed.com/viewjob?jk=abc123",
            company="TotallyUnknown12345",
        )
        assert result["resolution"] == "unresolved"

    @pytest.mark.asyncio
    async def test_company_careers_fallback(self, mock_page):
        # Known company, non-ATS careers page
        mock_page.goto = AsyncMock(side_effect=Exception("timeout"))

        result = await resolve_apply_url(
            mock_page,
            job_url="https://www.indeed.com/viewjob?jk=abc123",
            company="Meta",
        )
        # Should use company lookup since redirect failed
        assert result["company_careers"] is not None
        assert "metacareers" in result["company_careers"]


# ─── Data completeness ──────────────────────────────────────────────────────

class TestDataCompleteness:
    def test_ats_domains_not_empty(self):
        assert len(ATS_DOMAINS) > 10

    def test_aggregator_domains_not_empty(self):
        assert len(AGGREGATOR_DOMAINS) > 5

    def test_company_map_not_empty(self):
        assert len(COMPANY_ATS_MAP) > 20

    def test_no_overlap_ats_aggregator(self):
        """ATS and aggregator sets should never overlap."""
        overlap = ATS_DOMAINS & AGGREGATOR_DOMAINS
        assert overlap == set(), f"Overlap: {overlap}"

    def test_company_map_urls_valid(self):
        """All company map URLs should start with https://."""
        for company, url in COMPANY_ATS_MAP.items():
            assert url.startswith("https://"), f"{company}: {url}"
