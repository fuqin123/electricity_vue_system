#!/usr/bin/env python
"""Django 管理入口。

常用命令：
  python manage.py migrate                     建表
  python manage.py seed_registry               按 config/settings.yaml 同步网关与计量点
  python manage.py collect                     采一轮（13 路电表 + 蒸汽总表）
  python manage.py collect --gateway GW-PDB-A   只采某个网关
  python manage.py aggregate --recent 6         算最近 6 个小时的小时差值
  python manage.py collect_loop                 常驻采集（生产环境用计划任务更稳）
  python manage.py runserver 0.0.0.0:8000      起接口服务
"""
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eep.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "未找到 Django。请先安装依赖：\n"
            "  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
