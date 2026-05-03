"""CORS allowlist comes from `CORS_ORIGINS`; falls back to `*` for the demo
path. Pin the parser so a stray comma or whitespace doesn't quietly admit
an empty origin."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.main import _cors_origins


class CorsOriginsTest(unittest.TestCase):
    def test_default_is_star(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORS_ORIGINS", None)
            self.assertEqual(_cors_origins(), ["*"])

    def test_blank_falls_back_to_star(self) -> None:
        with mock.patch.dict(os.environ, {"CORS_ORIGINS": "   "}):
            self.assertEqual(_cors_origins(), ["*"])

    def test_single_origin(self) -> None:
        with mock.patch.dict(os.environ, {"CORS_ORIGINS": "https://retail.example.com"}):
            self.assertEqual(_cors_origins(), ["https://retail.example.com"])

    def test_comma_split_with_whitespace(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CORS_ORIGINS": " https://a.example.com , https://b.example.com "},
        ):
            self.assertEqual(
                _cors_origins(),
                ["https://a.example.com", "https://b.example.com"],
            )

    def test_empty_segments_dropped(self) -> None:
        # A stray trailing comma must not produce an empty-string origin
        # (which would, depending on the CORS middleware version, behave
        # like `*` and undermine the allowlist).
        with mock.patch.dict(os.environ, {"CORS_ORIGINS": "https://a.example.com,, "}):
            self.assertEqual(_cors_origins(), ["https://a.example.com"])


if __name__ == "__main__":
    unittest.main()
