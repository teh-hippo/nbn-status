"""Tests for ``nbn_monitor.config``.

Covers address loading from the environment and the operator-log redaction
contract defined here.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import nbn_monitor

from .conftest import MAINTENANCE_OK, SAMPLE_ADDRESSES_JSON

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadAddresses:
    def test_loads_from_env(self) -> None:
        with patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}):
            addrs = nbn_monitor.load_addresses()
        assert len(addrs) == 3
        assert addrs[0].label == "Home"
        assert addrs[0].poll is True
        assert addrs[0].notify is True
        assert addrs[1].compare is True
        assert addrs[2].poll is False

    def test_missing_env_exits(self) -> None:
        with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit):
            nbn_monitor.load_addresses()


class TestLogRedaction:
    """Operator-visible logs must never carry user-facing labels or LOC ids."""

    _LEAKY_ADDRESSES_JSON = json.dumps(
        [
            {
                "label": "123 Bailey Street",
                "loc_id": "LOC000000000077",
                "poll": True,
                "notify": True,
            },
        ]
    )

    def test_poll_cycle_does_not_leak_label(
        self,
        state_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.dict(os.environ, {"NBN_ADDRESSES": self._LEAKY_ADDRESSES_JSON}),
            patch.object(nbn_monitor.api.niquests, "Session") as mock_cls,
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
        ):
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            addresses = nbn_monitor.load_addresses()
            nbn_monitor.run_poll_cycle(addresses)

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Bailey" not in combined
        assert "123 Bailey Street" not in combined
        assert "LOC000000000077" not in combined
        assert "addr_0" in combined
