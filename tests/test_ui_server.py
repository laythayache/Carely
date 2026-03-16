"""Tests for UI server hardware button ingestion helpers."""

import asyncio

import pytest

from src.ui_server import UIServer


@pytest.mark.asyncio
async def test_button_payload_press_enqueues_button_event():
    queue = asyncio.Queue()
    server = UIServer(
        host="127.0.0.1",
        port=8080,
        event_queue=queue,
        button_api_enabled=True,
        button_api_bearer_token="secret-token",
    )

    accepted = await server._handle_button_payload({"action": "press"})

    assert accepted is True
    event_type, event_data = queue.get_nowait()
    assert event_type == "button"
    assert event_data == "press"


@pytest.mark.asyncio
async def test_button_payload_legacy_event_enqueues_button_event():
    queue = asyncio.Queue()
    server = UIServer(
        host="127.0.0.1",
        port=8080,
        event_queue=queue,
        button_api_enabled=True,
        button_api_bearer_token="secret-token",
    )

    accepted = await server._handle_button_payload({"event": "button_press"})

    assert accepted is True
    event_type, event_data = queue.get_nowait()
    assert event_type == "button"
    assert event_data == "press"


@pytest.mark.asyncio
async def test_button_payload_invalid_is_rejected():
    queue = asyncio.Queue()
    server = UIServer(
        host="127.0.0.1",
        port=8080,
        event_queue=queue,
        button_api_enabled=True,
        button_api_bearer_token="secret-token",
    )

    accepted = await server._handle_button_payload({"action": "long_press"})

    assert accepted is False
    assert queue.empty()


def test_is_authorized_accepts_matching_bearer_token():
    server = UIServer(
        host="127.0.0.1",
        port=8080,
        event_queue=asyncio.Queue(),
        button_api_enabled=True,
        button_api_bearer_token="secret-token",
    )

    assert server._is_authorized("Bearer secret-token") is True


def test_is_authorized_rejects_wrong_or_missing_token():
    server = UIServer(
        host="127.0.0.1",
        port=8080,
        event_queue=asyncio.Queue(),
        button_api_enabled=True,
        button_api_bearer_token="secret-token",
    )

    assert server._is_authorized("") is False
    assert server._is_authorized("Bearer wrong-token") is False
