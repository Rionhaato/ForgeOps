import json

import forgeops.integrations.boardroom as boardroom
from forgeops.integrations.boardroom import post_dispatch, render_dispatch


class FakeResponse:
    def __init__(self, payload: dict | bytes):
        self.payload = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


def test_render_dispatch_matches_boardroom_task_shape():
    spec = {
        "id": "FO-007",
        "title": "Boardroom relay",
        "owner": "Codex",
        "reviewer": "Claude",
        "objective": "Post structured dispatches without manual copy-paste.",
        "authorized": ["Render the message.", "Post it through Slack."],
        "acceptance": ["All required sections are present."],
        "forbidden": ["Never expose a token."],
        "cleanup": ["Keep the worktree."],
    }

    assert render_dispatch(spec) == (
        "[FORGEOPS TASK] [CODEX DISPATCH]\n"
        "ID: FO-007\n"
        "Title: Boardroom relay\n"
        "Owner: Codex\n"
        "Reviewer: Claude\n"
        "\n"
        "Objective:\n"
        "Post structured dispatches without manual copy-paste.\n"
        "\n"
        "Authorized:\n"
        "1. Render the message.\n"
        "2. Post it through Slack.\n"
        "\n"
        "Acceptance:\n"
        "\u2022 All required sections are present.\n"
        "\n"
        "Forbidden / dangerous:\n"
        "\u2022 Never expose a token.\n"
        "\n"
        "Cleanup:\n"
        "\u2022 Keep the worktree."
    )


def test_post_dispatch_posts_expected_slack_request(monkeypatch):
    token = "fixture-token"
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", token)
    observed = {}

    def fake_urlopen(request, *, timeout):
        observed["request"] = request
        observed["timeout"] = timeout
        return FakeResponse({"ok": True, "channel": "C0BMESXCR8A", "ts": "123.456"})

    monkeypatch.setattr(boardroom, "urlopen", fake_urlopen)

    result = post_dispatch("Dispatch text")

    request = observed["request"]
    assert result == {"ok": True, "ts": "123.456", "error": None}
    assert request.full_url == "https://slack.com/api/chat.postMessage"
    assert request.method == "POST"
    assert request.get_header("Authorization") == f"Bearer {token}"
    assert request.get_header("Content-type") == "application/json; charset=utf-8"
    assert json.loads(request.data) == {
        "channel": "C0BMESXCR8A",
        "text": "Dispatch text",
    }
    assert observed["timeout"] > 0


def test_post_dispatch_missing_token_never_opens_network(monkeypatch):
    monkeypatch.delenv("FORGEOPS_SLACK_BOT_TOKEN", raising=False)

    def unexpected_urlopen(*args, **kwargs):
        raise AssertionError("network boundary must not be called")

    monkeypatch.setattr(boardroom, "urlopen", unexpected_urlopen)

    assert post_dispatch("Dispatch text") == {
        "ok": False,
        "ts": None,
        "error": "missing_token",
    }


def test_post_dispatch_normalizes_slack_api_error(monkeypatch):
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(
        boardroom,
        "urlopen",
        lambda request, *, timeout: FakeResponse(
            {"ok": False, "ts": "unexpected", "error": "channel_not_found"}
        ),
    )

    assert post_dispatch("Dispatch text") == {
        "ok": False,
        "ts": None,
        "error": "channel_not_found",
    }


def test_post_dispatch_handles_invalid_json_response(monkeypatch):
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(
        boardroom,
        "urlopen",
        lambda request, *, timeout: FakeResponse(b"not-json"),
    )

    assert post_dispatch("Dispatch text") == {
        "ok": False,
        "ts": None,
        "error": "invalid_response",
    }


def test_post_dispatch_handles_transport_failure_without_leaking_token(monkeypatch):
    token = "fixture-token"
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", token)

    def failing_urlopen(request, *, timeout):
        raise RuntimeError(f"transport failed with {token}")

    monkeypatch.setattr(boardroom, "urlopen", failing_urlopen)

    result = post_dispatch("Dispatch text")

    assert result == {"ok": False, "ts": None, "error": "request_failed"}
    assert token not in repr(result)


def test_post_dispatch_handles_unexpected_response_shape(monkeypatch):
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(
        boardroom,
        "urlopen",
        lambda request, *, timeout: FakeResponse(b"[]"),
    )

    assert post_dispatch("Dispatch text") == {
        "ok": False,
        "ts": None,
        "error": "invalid_response",
    }


def test_post_dispatch_reads_selected_token_environment_at_each_call(monkeypatch):
    authorizations = []

    def fake_urlopen(request, *, timeout):
        authorizations.append(request.get_header("Authorization"))
        return FakeResponse({"ok": True, "ts": "123.456"})

    monkeypatch.setattr(boardroom, "urlopen", fake_urlopen)
    monkeypatch.setenv("FORGEOPS_TEST_SLACK_TOKEN", "first-fixture")
    first = post_dispatch(
        "First dispatch",
        channel_id="C123TEST",
        token_env="FORGEOPS_TEST_SLACK_TOKEN",
    )
    monkeypatch.setenv("FORGEOPS_TEST_SLACK_TOKEN", "second-fixture")
    second = post_dispatch(
        "Second dispatch",
        channel_id="C123TEST",
        token_env="FORGEOPS_TEST_SLACK_TOKEN",
    )

    assert first == {"ok": True, "ts": "123.456", "error": None}
    assert second == {"ok": True, "ts": "123.456", "error": None}
    assert authorizations == ["Bearer first-fixture", "Bearer second-fixture"]


def test_post_dispatch_never_raises_when_request_construction_fails(monkeypatch):
    monkeypatch.setenv("FORGEOPS_SLACK_BOT_TOKEN", "fixture-token")

    def failing_request(*args, **kwargs):
        raise ValueError("invalid request")

    monkeypatch.setattr(boardroom, "Request", failing_request)

    assert post_dispatch("Dispatch text") == {
        "ok": False,
        "ts": None,
        "error": "request_failed",
    }
