"""Application project IDs stay separate from platform-owned project numbers."""

import pytest

from agent.config import project_id


def test_explicit_project_id_overrides_platform_project_number(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "1001206471385")
    monkeypatch.setenv("CURBSIDE_PROJECT_ID", "qwiklabs-gcp-04-e3eaf2b4f0c3")
    assert project_id() == "qwiklabs-gcp-04-e3eaf2b4f0c3"


def test_local_google_project_id_still_works(monkeypatch):
    monkeypatch.delenv("CURBSIDE_PROJECT_ID", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "local-project")
    assert project_id() == "local-project"


@pytest.mark.parametrize("value", ["", "1001206471385", "bad`project", "../project"])
def test_invalid_explicit_project_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CURBSIDE_PROJECT_ID", value)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "valid-project")
    with pytest.raises(ValueError, match="CURBSIDE_PROJECT_ID"):
        project_id()
