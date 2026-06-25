from .app import _bootstrap_persistence, create_demo, initialize_model_catalog


def main() -> None:
	_bootstrap_persistence()
	initialize_model_catalog()
	create_demo().queue().launch()


if __name__ == "__main__":
	main()
