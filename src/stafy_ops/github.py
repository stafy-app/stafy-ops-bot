import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from stafy_ops.config import Settings
from stafy_ops.render import render_body
from stafy_ops.schemas import MILESTONE_REPOS, IssueDraft

_REST = "https://api.github.com"

_PROJECT_QUERY = """
query($org: String!, $n: Int!) {
  organization(login: $org) {
    projectV2(number: $n) {
      id
      fields(first: 50) { nodes { ... on ProjectV2SingleSelectField { id name options { id name } } } }
    }
  }
}"""
_ADD_ITEM = """
mutation($p: ID!, $c: ID!) {
  addProjectV2ItemById(input: {projectId: $p, contentId: $c}) { item { id } }
}"""
_SET_OPTION = """
mutation($p: ID!, $i: ID!, $f: ID!, $o: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $p, itemId: $i, fieldId: $f, value: {singleSelectOptionId: $o}}) {
    projectV2Item { id }
  }
}"""


@dataclass
class CreatedIssue:
    url: str
    warnings: list[str] = field(default_factory=list)
    number: int = 0
    repo: str = ""


class IssueCreator(Protocol):
    async def resolve_milestone(self, draft: IssueDraft) -> str | None: ...
    async def create_issue(self, draft: IssueDraft) -> CreatedIssue: ...
    async def link_related(self, pairs: list[tuple[IssueDraft, CreatedIssue]]) -> list[str]: ...


def _semver(tag: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


class GitHubClient:
    """Creates the issue, then sets Priority (org issue field, on the issue) and Status/Size (Project fields).

    Failures after the issue exists are returned as warnings, never raised, so the issue is not lost.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._s = settings
        self._client = client or httpx.AsyncClient(timeout=30)
        self._headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._priority_field_id: int | None = None
        self._project: dict | None = None
        self._login: str | None = None

    async def _graphql(self, query: str, variables: dict) -> dict:
        resp = await self._client.post(f"{_REST}/graphql", headers=self._headers, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"][0]["message"])
        return body["data"]

    async def _priority_field(self) -> int:
        if self._priority_field_id is None:
            resp = await self._client.get(f"{_REST}/orgs/{self._s.github_org}/issue-fields", headers=self._headers)
            resp.raise_for_status()
            self._priority_field_id = next(f["id"] for f in resp.json() if f["name"] == "Priority")
        return self._priority_field_id

    async def _project_meta(self) -> dict:
        if self._project is None:
            data = await self._graphql(_PROJECT_QUERY, {"org": self._s.github_org, "n": self._s.github_project_number})
            project = data["organization"]["projectV2"]
            fields = {n["name"]: n for n in project["fields"]["nodes"] if n.get("name")}
            self._project = {"id": project["id"], "fields": fields}
        return self._project

    async def _set_project_option(self, project: dict, item_id: str, field_name: str, option: str) -> None:
        fld = project["fields"][field_name]
        option_id = next(o["id"] for o in fld["options"] if o["name"] == option)
        await self._graphql(_SET_OPTION, {"p": project["id"], "i": item_id, "f": fld["id"], "o": option_id})

    async def _get_list(self, path: str, params: dict) -> list[dict]:
        resp = await self._client.get(f"{_REST}/repos/{self._s.github_org}/{path}", headers=self._headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def resolve_milestone(self, draft: IssueDraft) -> str | None:
        """Explicit request wins; else lowest open vX.Y.Z milestone; else latest tag with minor + 1; else None. GitHub is the only source."""
        if draft.repo not in MILESTONE_REPOS:
            return None
        if draft.milestone:
            return draft.milestone
        repo = draft.repo.value
        open_ms = [v for m in await self._get_list(f"{repo}/milestones", {"state": "open", "per_page": 100}) if (v := _semver(m["title"]))]
        if open_ms:
            major, minor, patch = min(open_ms)
            return f"v{major}.{minor}.{patch}"
        tags = [v for t in await self._get_list(f"{repo}/tags", {"per_page": 100}) if (v := _semver(t["name"]))]
        if tags:
            major, minor, _ = max(tags)
            return f"v{major}.{minor + 1}.0"
        return None

    async def _viewer_login(self) -> str:
        """The token owner — issues are always assigned to whoever runs the bot."""
        if self._login is None:
            resp = await self._client.get(f"{_REST}/user", headers=self._headers)
            resp.raise_for_status()
            self._login = resp.json()["login"]
        return self._login

    async def _milestone_number(self, repo: str, title: str) -> int:
        for m in await self._get_list(f"{repo}/milestones", {"state": "all", "per_page": 100}):
            if m["title"] == title:
                return m["number"]
        resp = await self._client.post(
            f"{_REST}/repos/{self._s.github_org}/{repo}/milestones", headers=self._headers, json={"title": title}
        )
        resp.raise_for_status()
        return resp.json()["number"]

    async def link_related(self, pairs: list[tuple[IssueDraft, CreatedIssue]]) -> list[str]:
        """Appends a `## Related` section (cross-repo `org/repo#n` references) to each issue of a multi-issue request."""
        warnings: list[str] = []
        for draft, created in pairs:
            others = [f"- {self._s.github_org}/{c.repo}#{c.number}" for _, c in pairs if c is not created]
            body = f"{render_body(draft)}\n## Related\n" + "\n".join(others) + "\n"
            try:
                resp = await self._client.patch(
                    f"{_REST}/repos/{self._s.github_org}/{created.repo}/issues/{created.number}",
                    headers=self._headers,
                    json={"body": body},
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — issues exist; report instead of failing
                warnings.append(f"Related links not set on {created.repo}#{created.number}: {exc}")
        return warnings

    async def create_issue(self, draft: IssueDraft) -> CreatedIssue:
        payload: dict ={"title": draft.title, "body": render_body(draft), "labels": draft.labels}
        warnings: list[str] = []
        try:
            if title := draft.milestone or await self.resolve_milestone(draft):
                payload["milestone"] = await self._milestone_number(draft.repo.value, title)
        except Exception as exc:  # noqa: BLE001 — still create the issue, report the miss
            warnings.append(f"Milestone not set: {exc}")
        try:
            payload["assignees"] = [await self._viewer_login()]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Assignee not set: {exc}")
        resp = await self._client.post(
            f"{_REST}/repos/{self._s.github_org}/{draft.repo.value}/issues", headers=self._headers, json=payload
        )
        resp.raise_for_status()
        issue = resp.json()
        created = CreatedIssue(url=issue["html_url"], warnings=warnings, number=issue["number"], repo=draft.repo.value)

        try:
            resp = await self._client.post(
                f"{_REST}/repos/{self._s.github_org}/{draft.repo.value}/issues/{issue['number']}/issue-field-values",
                headers=self._headers,
                json={"issue_field_values": [{"field_id": await self._priority_field(), "value": draft.priority.value}]},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — issue already exists; report instead of failing
            created.warnings.append(f"Priority not set: {exc}")

        try:
            project = await self._project_meta()
            added = await self._graphql(_ADD_ITEM, {"p": project["id"], "c": issue["node_id"]})
            item_id = added["addProjectV2ItemById"]["item"]["id"]
            await self._set_project_option(project, item_id, "Status", "Backlog")
            await self._set_project_option(project, item_id, "Size", draft.size.value)
        except Exception as exc:  # noqa: BLE001
            created.warnings.append(f"Project fields not set: {exc}")
        return created
