"""PyInstaller 入口：Umless.exe（无控制台）与 umless-cli.exe（控制台）共用这一个脚本。"""

from umless.__main__ import main

if __name__ == "__main__":
    main()
