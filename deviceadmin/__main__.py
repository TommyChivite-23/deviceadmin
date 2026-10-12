"""``python -m deviceadmin`` - run the development server."""

from .app import create_app
from .config import Settings

if __name__ == "__main__":
    settings = Settings.from_env()
    create_app(settings).run(host="127.0.0.1", port=5000, debug=settings.debug)
