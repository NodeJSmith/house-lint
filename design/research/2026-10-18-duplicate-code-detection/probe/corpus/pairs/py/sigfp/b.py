async def test_beta_writes_state(hassette_harness: HassetteHarness, mock_client: MagicMock, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state.json"
    hassette_harness.config.state_path = state_file
    await hassette_harness.save_state({"light.kitchen": "on"})
    assert state_file.exists()
    mock_client.assert_not_called()
