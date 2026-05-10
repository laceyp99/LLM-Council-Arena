import importlib


def test_package_imports() -> None:
	arena_package = importlib.import_module("arena")
	arena_app = importlib.import_module("arena.app")

	assert arena_package is not None
	assert arena_app is not None


def test_main_entrypoint_launches_demo(monkeypatch) -> None:
	main_module = importlib.import_module("arena.__main__")
	launch_calls: list[str] = []

	class FakeQueuedDemo:
		def launch(self) -> None:
			launch_calls.append("launch")

	class FakeDemo:
		def queue(self) -> FakeQueuedDemo:
			launch_calls.append("queue")
			return FakeQueuedDemo()

	monkeypatch.setattr(main_module, "demo", FakeDemo())

	main_module.main()

	assert launch_calls == ["queue", "launch"]
