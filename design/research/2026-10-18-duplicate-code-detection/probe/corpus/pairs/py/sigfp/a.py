async def test_alpha_reconnects(hassette_harness: HassetteHarness, mock_client: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HASSETTE_TOKEN", "abc")
    await hassette_harness.start()
    mock_client.disconnect()
    assert await hassette_harness.wait_connected(timeout=5)
    assert "reconnected" in caplog.text
