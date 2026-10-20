"""Keyless local ADC and deployed runtime use the same identity boundary."""

from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials

from agent import data_access
from agent.evidence import toolbox_headers


def test_user_adc_uses_runtime_then_auditor():
    user = Credentials("user-token")
    runtime = MagicMock()
    auditor = MagicMock()
    with (
        patch.object(data_access, "default", return_value=(user, "p")),
        patch.object(
            data_access.impersonated_credentials,
            "Credentials",
            side_effect=[runtime, auditor],
        ) as impersonate,
        patch.object(data_access.bigquery, "Client") as client,
    ):
        data_access.impersonated_client("auditor")
    assert (
        impersonate.call_args_list[0]
        .kwargs["target_principal"]
        .startswith("curbside-agent@")
    )
    assert impersonate.call_args_list[1].kwargs["source_credentials"] is runtime
    assert (
        impersonate.call_args_list[1]
        .kwargs["target_principal"]
        .startswith("curbside-auditor@")
    )
    assert client.call_args.kwargs["credentials"] is auditor


def test_private_toolbox_token_supports_user_adc():
    with (
        patch.object(
            data_access, "default", return_value=(Credentials("user-token"), "p")
        ),
        patch.object(
            data_access.impersonated_credentials,
            "Credentials",
            return_value=MagicMock(),
        ),
        patch.object(
            data_access.impersonated_credentials, "IDTokenCredentials"
        ) as token,
    ):
        token.return_value.token = "id-token"
        assert toolbox_headers("https://toolbox.example.run.app") == {
            "Authorization": "Bearer id-token"
        }
        assert (
            token.call_args.kwargs["target_audience"]
            == "https://toolbox.example.run.app"
        )
