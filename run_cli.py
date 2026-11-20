"""命令行入口（保留控制台，方便看日志）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcmoddl.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
