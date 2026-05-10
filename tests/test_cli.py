"""Tests for ``nbn_monitor.cli``.

Covers the argparse-based ``main`` entry point that the ``python -m
nbn_monitor`` shim invokes.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import nbn_monitor

from .conftest import SAMPLE_ADDRESSES_JSON


class TestMain:
    def test_main_poll(self, addresses: list[nbn_monitor.Address]) -> None:
        del addresses
        with (
            patch("sys.argv", ["nbn_monitor.py"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor.cli, "poll") as mock_poll,
        ):
            mock_poll.return_value = []
            nbn_monitor.main()
            mock_poll.assert_called_once()
            assert mock_poll.call_args.kwargs.get("notify") is False

    def test_main_notify(self) -> None:
        with (
            patch("sys.argv", ["nbn_monitor.py", "--notify"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor.cli, "poll") as mock_poll,
        ):
            mock_poll.return_value = []
            nbn_monitor.main()
            assert mock_poll.call_args.kwargs.get("notify") is True

    def test_main_serve(self) -> None:
        with (
            patch("sys.argv", ["nbn_monitor.py", "--serve"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor.cli, "serve") as mock_serve,
        ):
            nbn_monitor.main()
            mock_serve.assert_called_once()
