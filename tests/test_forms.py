"""Tests for the SusiConnectionForm validation logic (no database needed)."""

from interpretation.forms import SusiConnectionForm


def test_base_url_trailing_slash_is_stripped():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com/",
            "auth_token": "",
            "is_enabled": False,
        }
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["base_url"] == "https://susi.example.com"


def test_enabling_without_token_is_rejected():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com",
            "auth_token": "",
            "is_enabled": True,
        }
    )
    assert not form.is_valid()
    assert "auth_token" in form.errors


def test_enabling_with_token_is_accepted():
    form = SusiConnectionForm(
        data={
            "base_url": "https://susi.example.com",
            "auth_token": "tok",
            "is_enabled": True,
        }
    )
    assert form.is_valid(), form.errors


def test_base_url_is_required():
    form = SusiConnectionForm(
        data={"base_url": "", "auth_token": "", "is_enabled": False}
    )
    assert not form.is_valid()
    assert "base_url" in form.errors
