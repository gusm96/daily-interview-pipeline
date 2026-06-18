from unittest.mock import patch
import pytest
import main

REQUIRED = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]


def test_validate_env_passes_when_all_set(monkeypatch):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    assert main.validate_env() == []


def test_validate_env_reports_missing(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    missing = main.validate_env()
    assert "SLACK_BOT_USER_ID" in missing
    assert "GITHUB_TOKEN" not in missing


def test_generate_questions_returns_category_tuples(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = '[{"category":"🖥️ CS (네트워크/OS)","question":"Q1"},{"category":"☕ Java","question":"Q2"}]'
    with patch("main.call_gemini", return_value=fake):
        result = main.generate_questions("기존 readme")
    assert ("🖥️ CS (네트워크/OS)", "Q1") in result
    assert len(result) == 2
