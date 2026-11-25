import os
import sqlite3
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "探栏.db"

# 争用下等锁的最长时间：切流账册 / 结栏 / 回刷共用同一把 SQLite 写锁。
锁超时秒 = 30.0


def 连接() -> sqlite3.Connection:
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=锁超时秒)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(f"PRAGMA busy_timeout = {int(锁超时秒 * 1000)}")
    return c


def 事务连接() -> sqlite3.Connection:
    """取一枚立刻上写锁（BEGIN IMMEDIATE）的连接。

    结栏物化 + 缓冲消耗、契约切流都走这里，保证“读契约针 → 分组 → 写键 →
    消耗缓冲 → 写批次”整段串行，杜绝不加锁时的双成功/双键。
    调用方负责 COMMIT / ROLLBACK / close。
    """
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=锁超时秒, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(f"PRAGMA busy_timeout = {int(锁超时秒 * 1000)}")
    c.execute("BEGIN IMMEDIATE")
    return c
