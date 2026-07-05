"""命令行入口桥接：允许通过 python -m imsdt_demo 直接运行演示流程。"""

from imsdt_demo.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
