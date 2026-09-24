"""
pr_adapter.py
=============
Pull request adapter — create, review, and manage GitHub PRs via the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PullRequest:
    """Represents a GitHub Pull Request."""

    number: int
    title: str
    state: str
    html_url: str
    head_ref: str
    base_ref: str
    body: str = ""
    user: str = ""
    labels: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)


class GitHubAPIError(Exception):
    """Raised when a GitHub API call fails."""


class PullRequestAdapter:
    """Manages GitHub Pull Requests via the REST API.

    Parameters
    ----------
    token:
        GitHub personal access token or app token.
    base_url:
        GitHub API base URL (default: api.github.com).
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._log = logger.bind(component="PullRequestAdapter")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def create_pr(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str = "main",
        body: str = "",
    ) -> PullRequest:
        """Create a new pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: PR title.
            head: Source branch.
            base: Target branch.
            body: PR description.

        Returns:
            The created ``PullRequest``.
        """
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code != 201:
                raise GitHubAPIError(f"Failed to create PR: {resp.status_code} {resp.text[:500]}")
            data = resp.json()

        pr = self._parse_pr(data)
        self._log.info("pr.created", number=pr.number, title=pr.title)
        return pr

    async def list_prs(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        limit: int = 30,
    ) -> list[PullRequest]:
        """List pull requests for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: PR state filter (open, closed, all).
            limit: Maximum results.

        Returns:
            List of ``PullRequest`` objects.
        """
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": min(limit, 100)}

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            data = resp.json()

        return [self._parse_pr(item) for item in data[:limit]]

    async def get_pr(self, owner: str, repo: str, number: int) -> PullRequest:
        """Get a specific pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: PR number.

        Returns:
            The ``PullRequest``.
        """
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{number}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        return self._parse_pr(data)

    async def add_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
    ) -> dict[str, Any]:
        """Add a comment to a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: PR number.
            body: Comment text.

        Returns:
            The created comment data.
        """
        url = f"{self._base_url}/repos/{owner}/{repo}/issues/{number}/comments"

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json={"body": body})
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _parse_pr(data: dict[str, Any]) -> PullRequest:
        """Parse GitHub API PR data into a ``PullRequest`` object."""
        return PullRequest(
            number=data["number"],
            title=data["title"],
            state=data["state"],
            html_url=data["html_url"],
            head_ref=data["head"]["ref"],
            base_ref=data["base"]["ref"],
            body=data.get("body") or "",
            user=data.get("user", {}).get("login", ""),
            labels=[lb["name"] for lb in data.get("labels", [])],
            reviewers=[r["login"] for r in data.get("requested_reviewers", [])],
        )
