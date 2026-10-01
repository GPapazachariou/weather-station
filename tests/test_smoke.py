"""Smoke test to verify test discovery and pythonpath configuration."""

import app
import client
import protocol

import web


def test_imports():
    assert protocol.MAX_BATCH_SIZE == 50
    assert web.VALID_METRICS == {"temperature", "humidity", "windspeed"}
    assert app.PORT == 12345
    assert client.DEFAULT_SERVER_PORT == 12345
