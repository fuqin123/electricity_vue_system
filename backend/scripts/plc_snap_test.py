"""PLC 现场读点测试脚本（与现场验证过的 snap_test.py 一致）。

用途：产线未启动、只装了一个电眼时，先确认能连通 S7-200 SMART 并读到
DB1.DBD100 / DB1.DBD104 两个双整数（VD100 / VD104），再去后台配置点位。

用法（在装有 python-snap7==3.6.0 的环境/容器内）：
    python scripts/plc_snap_test.py
    python scripts/plc_snap_test.py --host 10.112.66.101 --db 1 --offset 100

⚠ S7-200 SMART 必须用 PG 连接类型（3），与后台实际采集 `services/plc.py`
  的 `set_connection_type(3)` 保持一致。
"""

from __future__ import annotations

import argparse

from snap7.client import Client
from snap7.type import Area
from snap7.util import get_dint


def main() -> None:
    parser = argparse.ArgumentParser(description="读取 S7-200 SMART DB 区两个双整数（VD 累计值）")
    parser.add_argument("--host", default="10.112.66.101", help="PLC IP（默认现场 10.112.66.101）")
    parser.add_argument("--rack", type=int, default=0, help="机架号（默认 0）")
    parser.add_argument("--slot", type=int, default=1, help="槽位号（默认 1）")
    parser.add_argument("--db", type=int, default=1, help="DB 块号（默认 1）")
    parser.add_argument("--offset", type=int, default=100, help="起始偏移（默认 100，即 VD100）")
    parser.add_argument("--count", type=int, default=2, help="连续读取的双整数个数（默认 2 → VD100 / VD104）")
    args = parser.parse_args()

    size = 4 * args.count
    client = Client()
    try:
        client.set_connection_type(3)
        client.connect(args.host, args.rack, args.slot)
        data = client.read_area(Area.DB, args.db, args.offset, size)
        for i in range(args.count):
            offset = args.offset + i * 4
            print(f"VD{offset} = {get_dint(data, i * 4)}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
