class TestLifecycle:
    async def test_does_not_reinitialize_pre_existing_instances(
        self,
        lifecycle_service: AppLifecycleService,
        mock_registry: MagicMock,
        mock_manifest: MagicMock,
        mock_factory: MagicMock,
        mock_hassette: MagicMock,
    ) -> None:
        """Pre-existing instances are not re-initialized."""
        mock_registry.running_indices.return_value = {0}
        await lifecycle_service.start_app("my_app")
        mock_factory.create.assert_called_once_with(mock_manifest, index=1)
        assert mock_hassette.bus.listeners == []
