import sys

from inventory import ITEMS, total


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(f"Items: {len(ITEMS)}, total: {total():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
