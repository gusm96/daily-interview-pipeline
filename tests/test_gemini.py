from unittest.mock import patch, MagicMock
import main


def _fake_resp(text, status=200):
    r = MagicMock()
    r.status_code = status
    r.ok = status == 200
    r.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }
    return r


def test_call_gemini_parses_and_strips(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("main.requests.post", return_value=_fake_resp("```markdown\n답변\n```")) as p:
        out = main.call_gemini("프롬프트", temperature=0.1)
    assert out == "답변"
    # generationConfig.temperature 전달 검증
    sent = p.call_args.kwargs["json"]
    assert sent["generationConfig"]["temperature"] == 0.1


def test_call_gemini_uses_model_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    with patch("main.requests.post", return_value=_fake_resp("x")) as p:
        main.call_gemini("p", temperature=0.4)
    assert "gemini-2.5-flash" in p.call_args.args[0]
