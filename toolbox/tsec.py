#!/usr/bin/env python
"""兼容入口：老的 `python tsec.py start xxx` 用法继续可用。

等价于 `python -m framework.cli ...`。
"""
import sys

from framework.cli import main

if __name__ == "__main__":
    main(sys.argv[1:])
