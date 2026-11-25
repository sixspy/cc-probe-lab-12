"""失败测乙：贯验把错键影子当契约对齐绿，则红。

分层：栏贯验 + 汇总键裁决（读路径）。
错键行即便在报文数上恰好与已汇总缓冲凑平，也不得被判成“契约对齐”。
影子/归档轨的报文数永远不得并入契约对齐口径。
"""
from __future__ import annotations

from app.db import 连接
from app.services import 装载, 结栏, 贯验, 目录, 夹具


def _灌3条河东PM25():
    装载.装一批({
        "批次编号": "乙",
        "报文": [
            {"报文编号": f"Y-{i}", "站点键": "站-河东", "指标键": "PM25",
             "读数": 20 + i, "观测时": "2026-11-24T08:10:00Z"}
            for i in range(3)
        ],
    })


def test_乙1_错键正式行凑平_贯验不得假绿():
    _灌3条河东PM25()
    conn = 连接()
    try:
        # 模拟旧实现/旁路写入：缓冲已消耗，但汇总编号是随机错键；报文数恰好=3
        conn.execute("UPDATE 入栏缓冲 SET 命运='已汇总'")
        conn.execute("UPDATE 观测报文 SET 命运='已汇总'")
        conn.execute(
            """INSERT INTO 站点汇总账(汇总编号,栏号,站点键,指标键,报文数,均值,物化时,命运)
               VALUES (?,?,?,?,?,?,?,?)""",
            ("汇-B-demo-站-河东-PM25-a1b2c3", "B-demo", "站-河东", "PM25",
             3, 21.0, "2026-11-25T00:00:00Z", "已汇总"),
        )
        conn.commit()
    finally:
        conn.close()

    果 = 贯验.跑一次()
    # 旧实现只比总数 3==3 -> 是否对齐=True（假绿），本断言先红
    assert 果["是否对齐"] is False
    # 契约口径不得把错键行计作契约汇总报文
    assert 果.get("契约汇总报文", 果["汇总报文"]) == 0
    键级 = 果.get("键级") or []
    命中 = [k for k in 键级 if k.get("站点键") == "站-河东" and k.get("指标键") == "PM25"]
    assert 命中, "贯验须给出键级详情"
    assert all(k.get("裁决") != "契约对齐" for k in 命中)


def test_乙2_影子归档不得充数为契约绿():
    _灌3条河东PM25()
    # 形态：缓冲已消耗，只有一条错键影子在归档轨
    conn = 连接()
    try:
        conn.execute("UPDATE 入栏缓冲 SET 命运='已汇总'")
        conn.execute("UPDATE 观测报文 SET 命运='已汇总'")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS 错键归档(
                 编号 INTEGER PRIMARY KEY AUTOINCREMENT,
                 归档号 TEXT, 来源批次 TEXT, 栏号 TEXT, 站点键 TEXT, 指标键 TEXT,
                 汇总编号 TEXT, 契约键 TEXT, 契约指纹 TEXT,
                 应有契约键 TEXT, 应有指纹 TEXT,
                 报文数 INTEGER, 均值 REAL, 载荷 TEXT, 裁决 TEXT, 时刻 TEXT)""",
        )
        conn.execute(
            """INSERT INTO 错键归档(归档号,栏号,站点键,指标键,汇总编号,契约键,
                 契约指纹,报文数,均值,裁决,时刻)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("档-1", "B-demo", "站-河东", "PM25",
             "汇-B-demo-站-河东-PM25-zz9999", "汇-B-demo-站-河东-PM25-zz9999",
             "指纹-错", 3, 21.0, "错键", "2026-11-25T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    果 = 贯验.只读快照()
    # 影子的 3 条不得让契约口径变绿：已汇总缓冲=3，契约现行汇总=0
    assert 果["是否对齐"] is False
    assert int(果.get("契约汇总报文", 0)) == 0
    assert int(果.get("影子报文", 0)) == 3 or len(果.get("影子", [])) == 1

    裁决 = 目录.汇总键裁决()
    assert 裁决["影子"], "裁决接口须列出错键影子"
    assert all(s.get("裁决") != "契约对齐" for s in 裁决["影子"])


def test_乙3_契约内成片_键级贯验真绿():
    装载.装一批(夹具.读取("入栏成汇总样例"))
    结栏.结当前栏()
    果 = 贯验.跑一次()
    assert 果["是否对齐"] is True
    assert 果["汇总报文"] == 3
    # 两个契约键都应在键级详情里判齐
    键级 = 果.get("键级") or []
    齐 = [k for k in 键级 if k.get("裁决") == "契约对齐"]
    assert len(齐) == 2
