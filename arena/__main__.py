from .app import _bootstrap_persistence, demo


def main() -> None:
	_bootstrap_persistence()
	demo.queue().launch()


if __name__ == "__main__":
	main()
