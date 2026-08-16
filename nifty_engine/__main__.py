"""Module entry — allows `python -m nifty_engine` to launch the CLI."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
