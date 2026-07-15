"""Entry point used when building a standalone executable with PyInstaller.

See PACKAGING.md. This intentionally launches the interactive menu (the CLI).
The Streamlit dashboard is not bundled — it stays a `pip install` feature.
"""

from flatfinder.cli import main

if __name__ == "__main__":
    main()
