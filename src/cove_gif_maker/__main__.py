import sys

from PySide6.QtWidgets import QApplication

from . import theme
from .app import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Cove GIF Maker")
    app.setOrganizationName("Cove")
    theme.apply(app)  # default cove blue-green accent — no longer user-configurable
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
