from .app import _bootstrap_persistence, create_demo, initialize_model_catalog
from .ui.config import APP_ICON_PATH


def main() -> None:
	_bootstrap_persistence()
	initialize_model_catalog()
	create_demo().queue().launch(favicon_path=APP_ICON_PATH)


if __name__ == "__main__":
	main()
