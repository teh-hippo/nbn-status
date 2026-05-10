"""Tests for ``function_app``.

Covers the Azure Functions HTTP route, including the Easy Auth
request-time guard that fails closed if Easy Auth is not configured on
the hosting Function App.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import azure.functions as func

import function_app
import nbn_monitor

from .conftest import SAMPLE_ADDRESSES_JSON

if TYPE_CHECKING:
    from pathlib import Path


def _seed_snapshot(state_file: Path, addresses: list[nbn_monitor.Address]) -> None:
    snapshot = nbn_monitor.derive_snapshot(
        [
            (
                addresses[0],
                nbn_monitor.OutageStatus(
                    loc_id=addresses[0].loc_id,
                    display_outage="NO_OUTAGE",
                    label="No outage",
                    checked_at=time.time(),
                ),
            )
        ],
        nbn_monitor.Snapshot.empty(),
        started_at="2025-01-01T00:00:00+00:00",
        completed_at="2025-01-01T00:00:01+00:00",
    )
    state_file.write_text(json.dumps(snapshot.to_dict()))


_STATUS_PAGE = function_app.status_page.build().get_user_function()


class TestStatusPage:
    def test_renders_when_easy_auth_enabled(
        self, addresses: list[nbn_monitor.Address], state_file: Path
    ) -> None:
        _seed_snapshot(state_file, addresses)
        with (
            patch.dict(
                os.environ,
                {
                    "NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON,
                    "WEBSITE_INSTANCE_ID": "instance-1",
                    "WEBSITE_AUTH_ENABLED": "true",
                },
            ),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
        ):
            response = _STATUS_PAGE(MagicMock(spec=func.HttpRequest))

        assert response.status_code == 200
        assert response.mimetype == "text/html"
        body = response.get_body().decode()
        assert "<!DOCTYPE html>" in body
        assert "Home" in body

    def test_returns_500_when_easy_auth_disabled_in_azure(
        self, addresses: list[nbn_monitor.Address], state_file: Path
    ) -> None:
        _seed_snapshot(state_file, addresses)
        with (
            patch.dict(
                os.environ,
                {
                    "NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON,
                    "WEBSITE_INSTANCE_ID": "instance-1",
                },
                clear=False,
            ),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
        ):
            os.environ.pop("WEBSITE_AUTH_ENABLED", None)
            response = _STATUS_PAGE(MagicMock(spec=func.HttpRequest))

        assert response.status_code == 500
        body = response.get_body().decode()
        assert "Easy Auth" in body

    def test_renders_locally_when_not_in_azure(
        self, addresses: list[nbn_monitor.Address], state_file: Path
    ) -> None:
        _seed_snapshot(state_file, addresses)
        env = {k: v for k, v in os.environ.items() if k != "WEBSITE_INSTANCE_ID"}
        env["NBN_ADDRESSES"] = SAMPLE_ADDRESSES_JSON
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
        ):
            response = _STATUS_PAGE(MagicMock(spec=func.HttpRequest))

        assert response.status_code == 200
        body = response.get_body().decode()
        assert "<!DOCTYPE html>" in body
