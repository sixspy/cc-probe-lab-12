"""栏贯验：缓冲消耗 ↔ 现行契约汇总 的键级互证。

旧口径只比“已汇总缓冲条数 == 汇总账报文数总和”，一条随机错键行只要报文数凑平
就会假绿。现在：
- 逐 (栏号,站点键,指标键) 对键；
- 只有通过汇总键契约裁决（同票 + 账册指纹）的现行行才计入契约口径；
- 错键正式行单列，错键归档影子永不计入对齐，只作影子披露。
"""
from __future__ import annotations

import json

from app.db import 连接
from app.seed import 此刻
from app.services import 汇总键契约 as KC


def 核算(conn) -> dict:
    针 = KC.现行契约(conn)
    fp = 针.get("契约指纹", KC.默认指纹)
    known = {r["契约指纹"] for r in conn.execute("SELECT 契约指纹 FROM 契约账册").fetchall()}

    在栏 = conn.execute(
        "SELECT COUNT(*) AS c FROM 入栏缓冲 WHERE 命运='在栏'"
    ).fetchone()["c"]
    已汇总缓冲 = conn.execute(
        "SELECT COUNT(*) AS c FROM 入栏缓冲 WHERE 命运='已汇总'"
    ).fetchone()["c"]
    坏点 = conn.execute("SELECT COUNT(*) AS c FROM 坏点匣").fetchone()["c"]

    exp_rows = conn.execute(
        """SELECT 栏号, 站点键, 指标键, COUNT(*) AS c
           FROM 入栏缓冲 WHERE 命运='已汇总'
           GROUP BY 栏号, 站点键, 指标键"""
    ).fetchall()
    期望: dict[tuple, int] = {
        (r["栏号"], r["站点键"], r["指标键"]): int(r["c"]) for r in exp_rows
    }

    formal = [dict(r) for r in conn.execute("SELECT * FROM 站点汇总账").fetchall()]
    现行行: list[dict] = []
    错键行: list[dict] = []
    实际: dict[tuple, int] = {}
    账列报文 = 0
    错键报文 = 0
    for r in formal:
        v = KC.裁决行(r, 针, known)
        账列报文 += int(r.get("报文数") or 0)
        k = (v["栏号"], v["站点键"], v["指标键"])
        if v["裁决"] in KC.对齐裁决:
            现行行.append(v)
            实际[k] = 实际.get(k, 0) + int(v["报文数"] or 0)
        else:
            错键行.append(v)
            错键报文 += int(v["报文数"] or 0)

    try:
        shadows = [dict(r) for r in conn.execute("SELECT * FROM 错键归档").fetchall()]
    except Exception:
        shadows = []
    影子报文 = sum(int(s.get("报文数") or 0) for s in shadows)

    # —— 键级详情：期望缓冲键 / 现行契约键 / 错键正式行 / 影子 ——
    keys = set(期望) | set(实际)
    for v in 错键行:
        keys.add((v["栏号"], v["站点键"], v["指标键"]))
    for s in shadows:
        keys.add((s["栏号"], s["站点键"], s["指标键"]))

    错键按组: dict[tuple, list] = {}
    for v in 错键行:
        错键按组.setdefault((v["栏号"], v["站点键"], v["指标键"]), []).append(v)
    影子按组: dict[tuple, list] = {}
    for s in shadows:
        影子按组.setdefault((s["栏号"], s["站点键"], s["指标键"]), []).append(s)

    键级 = []
    for k in sorted(keys):
        pen, st, me = k
        e = 期望.get(k, 0)
        a = 实际.get(k, 0)
        bads = 错键按组.get(k, [])
        shs = 影子按组.get(k, [])
        if bads:
            verdict = "错键"
        elif shs and a == 0:
            verdict = "仅错键影子"
        elif e == a and e > 0:
            verdict = "契约对齐"
        elif e == 0 and a == 0:
            verdict = "契约对齐"
        else:
            verdict = "键数不符"
        键级.append({
            "栏号": pen, "站点键": st, "指标键": me,
            "契约键": KC.规范编号(pen, st, me),
            "分组键": KC.分组串(pen, st, me),
            "缓冲报文数": e,
            "现行报文数": a,
            "影子报文数": sum(int(s.get("报文数") or 0) for s in shs),
            "错键正式行数": len(bads),
            "影子数": len(shs),
            "裁决": verdict,
            "错键": bads,
        })

    契约汇总报文 = sum(实际.values())
    键集齐 = 期望 == 实际
    契约对齐 = bool(键集齐 and not 错键行)
    数量对齐 = int(已汇总缓冲) == int(账列报文)

    if 契约对齐:
        说明 = f"契约键级互证一致（针 {fp}，{len(实际)} 键）"
    else:
        bits = []
        if not 键集齐:
            bits.append(f"键级撕裂：已汇总缓冲={已汇总缓冲} 契约汇总报文={契约汇总报文}")
        if 错键行:
            bits.append(f"{len(错键行)} 条错键正式行不得充契")
        if 影子报文:
            bits.append(f"另有错键影子 {len(shadows)} 条/{影子报文} 报文，不计入对齐")
        说明 = "；".join(bits) or "契约未对齐"

    return {
        "在栏": 在栏,
        "已汇总缓冲": int(已汇总缓冲),
        "汇总报文": int(契约汇总报文),          #  headline 改为契约口径
        "契约汇总报文": int(契约汇总报文),
        "账列报文": int(账列报文),
        "错键报文": int(错键报文),
        "影子报文": int(影子报文),
        "坏点": 坏点,
        "是否对齐": 契约对齐,
        "契约对齐": 契约对齐,
        "数量对齐": 数量对齐,
        "说明": 说明,
        "契约指纹": fp,
        "键级": 键级,
        "错键行": 错键行,
        "影子": shadows,
    }


def 跑一次() -> dict:
    conn = 连接()
    try:
        r = 核算(conn)
        时刻 = 此刻()
        conn.execute(
            """INSERT INTO 栏贯验(时刻,在栏笔数,汇总报文数,坏点笔数,是否对齐,说明,
                 契约指纹,契约对齐,契约汇总报文,错键报文,影子报文,键级报告)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                时刻, r["在栏"], r["汇总报文"], r["坏点"],
                1 if r["是否对齐"] else 0, r["说明"],
                r["契约指纹"], 1 if r["契约对齐"] else 0,
                r["契约汇总报文"], r["错键报文"], r["影子报文"],
                json.dumps(r["键级"], ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
        r["时刻"] = 时刻
        return r
    finally:
        conn.close()


def 只读快照() -> dict:
    """不落库的贯验快照，供页面预览。"""
    conn = 连接()
    try:
        return 核算(conn)
    finally:
        conn.close()
