# -*- coding: utf-8 -*-
"""
纸研社 启动程序
"""

from modules.report_importer_worker import maybe_run_from_argv


if __name__ == '__main__':
    if maybe_run_from_argv():
        raise SystemExit(0)
    from modules.app_shell import main
    main()