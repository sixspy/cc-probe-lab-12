"""争用失败测：契约切换 / 并发结栏导致双键现行，则红。

分层：结栏事务 + 契约账册 + 库内唯一约束。
硬推理：
- 写汇总键与缓冲消耗必须在同一写事务内同成败。
- 改契约与结栏并发，若不串行化会各写各的键 -> 双键。
- 不加锁则两个结栏请求都成功物化同一组 -> 双成功。
"""
from __future__ import annotations

import threading
import concurrent.futures as cf

from app.db import 连接
from app.services import 装载, 结栏, 目录


def _现行组(栏号: str):
    conn = 连接()
    try:
        rows = conn.execute(
            """SELECT 站点键, 指标键, COUNT(*) AS n,
                      GROUP_CONCAT(汇总编号) AS ids,
                      SUM(报文数) AS 报文
               FROM 站点汇总账
               WHERE 栏号=? AND COALESCE(轨,'现行')='现行'
               GROUP BY 站点键, 指标键""",
            (栏号,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _并发结一轮(轮: int, 线程数: int = 4):
    栏号 = f"B-c-{轮}"
    目录.改栏规({"当前栏号": 栏号})
    装载.装一批({
        "批次编号": f"争{轮}",
        "报文": [
            {"报文编号": f"C{轮}-{k}", "站点键": "站-河东", "指标键": "PM25",
             "读数": 20 + k, "观测时": "2026-11-24T08:10:00Z"}
            for k in range(3)
        ] + [
            {"报文编号": f"C{轮}-b", "站点键": "站-北岭", "指标键": "PM25",
             "读数": 28.0, "观测时": "2026-11-24T08:20:00Z"}
        ],
    })

    闸 = threading.Barrier(线程数)
    错误 = []

    def _跑(_):
        闸.wait()
        try:
            return 结栏.结当前栏()
        except Exception as e:  # 锁实现不应把 database is locked 抛给调用方
            错误.append(e)
            return None

    with cf.ThreadPoolExecutor(线程数) as ex:
        list(ex.map(_跑, range(线程数)))
    return 栏号, 错误


def test_丙1_并发双结栏不得双键现行():
    for 轮 in range(6):
        栏号, 错误 = _并发结一轮(轮)
        assert not 错误, f"并发结栏抛错: {错误!r}"
        组 = _现行组(栏号)
        for g in 组:
            assert g["n"] == 1, f"第{轮}轮出现双键现行: {g}"
            # 3 条河东 + 1 条北岭，键内报文数不得被并发翻倍
            if g["站点键"] == "站-河东":
                assert g["报文"] == 3
            else:
                assert g["报文"] == 1
            assert g["ids"] == f"汇-{栏号}-{g['站点键']}-{g['指标键']}"


def test_丙2_契约切换与结栏并发不得双键():
    for 轮 in range(6):
        栏号 = f"B-s-{轮}"
        目录.改栏规({"当前栏号": 栏号})
        装载.装一批({
            "批次编号": f"切{轮}",
            "报文": [
                {"报文编号": f"S{轮}-{k}", "站点键": "站-河东", "指标键": "PM25",
                 "读数": 21 + k, "观测时": "2026-11-24T08:10:00Z"}
                for k in range(2)
            ],
        })
        闸 = threading.Barrier(2)
        错误 = []

        def _结():
            闸.wait()
            try:
                结栏.结当前栏()
            except Exception as e:
                错误.append(e)

        def _切():
            闸.wait()
            try:
                目录.契约切流({"备注": f"并发切换{轮}"})
            except Exception as e:
                错误.append(e)

        t1 = threading.Thread(target=_结)
        t2 = threading.Thread(target=_切)
        t1.start(); t2.start(); t1.join(); t2.join()

        assert not 错误, f"切换/结栏并发抛错: {错误!r}"
        组 = _现行组(栏号)
        assert len(组) == 1
        assert 组[0]["n"] == 1, f"契约切换并发产生双键: {组}"

    # 账册任意时刻至多一枚现行指纹
    conn = 连接()
    try:
        现行针 = conn.execute(
            "SELECT COUNT(*) AS c FROM 契约账册 WHERE 状态='现行'"
        ).fetchone()["c"]
        assert 现行针 == 1
    finally:
        conn.close()


def test_丙3_库级唯一约束兜底():
    """即使绕过服务层，也无法在现行轨插入同组第二键。"""
    目录.改栏规({"当前栏号": "B-uq"})
    装载.装一批({
        "批次编号": "唯一",
        "报文": [
            {"报文编号": "U-1", "站点键": "站-河东", "指标键": "PM25",
             "读数": 22.0, "观测时": "2026-11-24T08:10:00Z"},
        ],
    })
    结栏.结当前栏()
    conn = 连接()
    try:
        import sqlite3
        with __import__("pytest").raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO 站点汇总账(汇总编号,栏号,站点键,指标键,报文数,均值,
                       物化时,命运,契约键,契约指纹,轨,裁决)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("另一个随机编号", "B-uq", "站-河东", "PM25", 1, 22.0,
                 "t", "已汇总", "汇-B-uq-站-河东-PM25", "x", "现行", "契约对齐"),
            )
            conn.commit()
        conn.rollback()
    finally:
        conn.close()
