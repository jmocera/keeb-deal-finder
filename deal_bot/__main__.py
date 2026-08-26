"""Enables `python -m deal_bot` as the CLI entry point."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bot",
        action="store_true",
        help="run the always-on Discord bot (owns the 4-hour schedule)",
    )
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    if args.bot:
        from deal_bot.bot import main as bot_main
        bot_main()
    else:
        from deal_bot.pipeline import main as pipeline_main
        pipeline_main()


if __name__ == "__main__":
    main()