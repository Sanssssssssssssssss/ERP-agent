"""Local model preparation and durable background learning jobs."""

import argparse
from pathlib import Path

from .store import digest, open_memory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["setup", "learn"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.operation == "setup":
        with open_memory(args.path, digest("model-setup"), staging=True, download=True):
            print("Local multilingual memory model ready")
    else:
        from .learning import learn

        learn(args.path)


if __name__ == "__main__":
    main()
