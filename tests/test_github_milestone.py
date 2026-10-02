import json
import httpx
import pytest

from stafy_ops.github import CreatedIssue, GitHubClient
from tests.conftest import make_draft


def client_with(milestones: list[str], tags: list[str], settings) -> GitHubClient:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/milestones"):
            return httpx.Response(200, json=[{"title": t, "number": i} for i, t in enumerate(milestones, 1)])
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json=[{"name": t} for t in tags])
        return httpx.Response(404)

    return GitHubClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))


@pytest.mark.parametrize("repo", ["stafy-landing", "workspace"])
async def test_unversioned_repos_never_get_a_milestone(settings, repo):
    gh = client_with(["v0.2.0"], ["v0.1.0"], settings)
    assert await gh.resolve_milestone(make_draft(repo=repo, milestone="v0.3.0")) is None


async def test_create_issue_assigns_token_owner_and_sets_milestone(settings):
    sent: dict = {}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "Kerolly"})
        if path.endswith("/milestones"):
            return httpx.Response(200, json=[{"title": "v0.2.0", "number": 5}])
        if path.endswith("/tags"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/issues"):
            sent.update(json.loads(request.content))
            return httpx.Response(201, json={"html_url": "https://x/1", "number": 1, "node_id": "N"})
        return httpx.Response(404)  # priority/project calls: reported as warnings

    gh = GitHubClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    created = await gh.create_issue(make_draft())
    assert sent["assignees"] == ["Kerolly"] and sent["milestone"] == 5
    assert created.url == "https://x/1"


async def test_link_related_patches_each_issue_with_the_others(settings):
    patched: dict[str, str] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        patched[request.url.path] = json.loads(request.content)["body"]
        return httpx.Response(200, json={})

    gh = GitHubClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    backend = (make_draft(repo="stafy-backend"), CreatedIssue(url="u1", number=10, repo="stafy-backend"))
    web = (make_draft(repo="stafy-web-app"), CreatedIssue(url="u2", number=20, repo="stafy-web-app"))
    assert await gh.link_related([backend, web]) == []
    assert "- stafy-app/stafy-web-app#20" in patched["/repos/stafy-app/stafy-backend/issues/10"]
    assert "- stafy-app/stafy-backend#10" in patched["/repos/stafy-app/stafy-web-app/issues/20"]
    assert "stafy-backend#10" not in patched["/repos/stafy-app/stafy-backend/issues/10"]


async def test_explicit_milestone_wins(settings):
    gh = client_with(["v0.2.0"], ["v0.1.0"], settings)
    assert await gh.resolve_milestone(make_draft(milestone="v0.5.0")) == "v0.5.0"


async def test_lowest_open_milestone(settings):
    gh = client_with(["v0.4.0", "v0.3.0", "notes"], ["v0.2.0"], settings)
    assert await gh.resolve_milestone(make_draft()) == "v0.3.0"


async def test_latest_tag_plus_one_minor(settings):
    gh = client_with([], ["v0.1.0", "v0.2.0", "latest"], settings)
    assert await gh.resolve_milestone(make_draft()) == "v0.3.0"


@pytest.mark.parametrize("tags", [[], ["nightly"]])
async def test_no_milestone_when_github_has_nothing(settings, tags):
    gh = client_with([], tags, settings)
    assert await gh.resolve_milestone(make_draft()) is None
