import pytest
import requests
import responses

from interpretation.api_key_validator import APIKeyNetworkError, validate_provider_key


@responses.activate
def test_validate_provider_key_success():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        json={"data": []},
        status=200,
    )
    result = validate_provider_key("openai", "sk-valid-key")
    assert result is True


@responses.activate
def test_validate_provider_key_401_fails():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        json={"error": "invalid_api_key"},
        status=401,
    )
    result = validate_provider_key("openai", "sk-invalid-key")
    assert result is False


@responses.activate
def test_validate_provider_key_403_fails():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        json={"error": "forbidden"},
        status=403,
    )
    result = validate_provider_key("openai", "sk-forbidden-key")
    assert result is False


@responses.activate
def test_validate_provider_key_network_timeout():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        body=requests.exceptions.Timeout("Connection timed out"),
    )
    with pytest.raises(APIKeyNetworkError):
        validate_provider_key("openai", "sk-timeout-key")


@responses.activate
def test_validate_provider_key_500_transient_error():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        body="Internal Server Error",
        status=500,
    )
    with pytest.raises(APIKeyNetworkError):
        validate_provider_key("openai", "sk-transient-key")


def test_validate_provider_key_empty():
    assert validate_provider_key("openai", "") is False


@responses.activate
def test_validate_provider_key_404_inconclusive():
    responses.add(
        responses.GET,
        "https://api.openai.com/v1/models",
        body="Not Found",
        status=404,
    )
    with pytest.raises(APIKeyNetworkError):
        validate_provider_key("openai", "sk-inconclusive-key")
