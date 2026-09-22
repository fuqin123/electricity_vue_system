"""EEp 后端工程包。

把 PyMySQL 注册成 MySQLdb，这样 Django 可以走纯 Python 的 MySQL 驱动，
不需要在 Windows 上编译 mysqlclient（省掉一堆 Visual C++ 编译依赖）。
"""

import pymysql

pymysql.install_as_MySQLdb()
