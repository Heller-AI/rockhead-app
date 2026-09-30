"""
Launcher for the Rock Head Gradient Analyzer.

PyInstaller can't bundle a Streamlit script directly because Streamlit
expects to be invoked via its CLI. This launcher invokes the CLI
programmatically so PyInstaller can wrap the whole thing into an .exe.
"""

import os
import sys
import time
import threading
import webbrowser

from streamlit.web import cli as stcli


def resource_path(relative: str) -> str:
    """Get absolute path to a bundled resource (works for dev and PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def open_browser_when_ready(url: str, delay: float = 3.0) -> None:
    """Open the user's default browser after a small delay."""
    time.sleep(delay)
    webbrowser.open(url)


def main() -> None:
    script_path = resource_path("rockhead_gradient_app.py")
    port = "8501"
    url = f"http://localhost:{port}"

    # Open browser in background while Streamlit starts
    threading.Thread(
        target=open_browser_when_ready, args=(url,), daemon=True
    ).start()

    # Hand over to Streamlit
    sys.argv = [
        "streamlit",
        "run",
        script_path,
        "--global.developmentMode=false",
        "--server.port", port,
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
        "--server.fileWatcherType=none",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
