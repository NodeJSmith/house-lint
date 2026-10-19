class TestShutdown:
    async def test_emits_stopped_event_for_discarded_entries(
        self,
        lifecycle_service: AppLifecycleService,
        mock_registry: MagicMock,
        mock_manifest: MagicMock,
        mock_factory: MagicMock,
        mock_hassette: MagicMock,
    ) -> None:
        """Discarded failed entries still emit a stopped event."""
        mock_registry.unregister_app.return_value = []
        result = await lifecycle_service.stop_app("other_app", reason="shutdown")
        assert result.stopped == 0
        mock_hassette.send_event.assert_awaited()
