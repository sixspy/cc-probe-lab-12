"""结栏：入栏缓冲按站点+指标汇总物化。

汇总键契约编排：
- 汇总编号与 (栏号,站点键,指标键) 分组键同票，确定性生成，不再带随机尾巴。
- 物化 + 缓冲消耗在同一个 BEGIN IMMEDIATE 写事务内同成败。
- 事务内读契约账册现行指纹；持错针/错键的结栏拒入现行、缓冲不消耗，错键留痕归档。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import defaultdict

from app.db import 事务连接
from app.seed import 此刻
from app.services import 汇总键契约 as KC


def _当前栏号(conn) -> str:
    r = conn.execute("SELECT 值 FROM 栏规 WHERE 键='当前栏号'").fetchone()
    return r["值"] if r else "B-demo"


def 结当前栏(体: dict | None = None, **额外) -> dict:
    体 = dict(体 or {})
    for k, v in (额外 or {}).items():
        if v is not None:
            体.setdefault(k, v)

    批 = f"结栏-{uuid.uuid4().hex[:8]}"
    时刻 = 此刻()
    强制错键 = bool(体.get("错键") or 体.get("故意错键") or 体.get("错键模式"))
    期望指纹 = (
        体.get("期望指纹")
        or 体.get("期望契约指纹")
        or 体.get("钉契约指纹")
        or 体.get("契约指纹")
    )

    conn = 事务连接()
    try:
        栏号 = _当前栏号(conn)
        针 = KC.现行契约(conn)
        现行指纹 = 针.get("契约指纹", KC.默认指纹)
        错针 = 期望指纹 is not None and str(期望指纹) != str(现行指纹)

        rows = conn.execute(
            "SELECT * FROM 入栏缓冲 WHERE 栏号=? AND 命运='在栏'", (栏号,)
        ).fetchall()
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for r in rows:
            groups[(r["站点键"], r["指标键"])].append(r)

        # —— 故意错键 / 错针：拒回刷现行，缓冲不消耗（同成败），错键留痕只读归档 ——
        if rows and (强制错键 or 错针):
            影子 = []
            for (站点, 指标), evs in groups.items():
                票 = KC.规范编号(栏号, 站点, 指标)
                if 强制错键:
                    错编号 = f"汇-{栏号}-{站点}-{指标}-{uuid.uuid4().hex[:6]}"
                    verdict = "错键"
                    why = "故意错键：汇总编号与分组键不同票"
                else:
                    错编号 = 票
                    verdict = "指纹不符"
                    why = f"持错针 {期望指纹}，现行针 {现行指纹}"
                均值 = sum(float(e["读数"]) for e in evs) / len(evs)
                档号 = KC.写归档(
                    conn, 栏号=栏号, 站点键=站点, 指标键=指标, 汇总编号=错编号,
                    来源批次=批, 契约指纹=str(期望指纹 or "错键模式"),
                    报文数=len(evs), 均值=均值, 裁决=verdict,
                    载荷={"原因": why, "期望指纹": 期望指纹}, 应有指纹=现行指纹,
                )
                影子.append({"归档号": 档号, "汇总编号": 错编号, "契约键": 票,
                             "裁决": verdict, "原因": why, "报文数": len(evs)})
            报告 = {
                "批次编号": 批, "栏号": 栏号, "汇总数": 0,
                "缓冲消耗": 0, "在栏未消耗": len(rows),
                "拒收": True, "拒绝": True,
                "错误": "汇总键契约裁决不通过，已拒回刷现行；缓冲未消耗，错键进只读归档",
                "裁决": "错键" if 强制错键 else "指纹不符",
                "契约指纹": 现行指纹, "期望指纹": 期望指纹,
                "影子": 影子, "明细": [], "空栏": False,
            }
            conn.execute(
                """INSERT INTO 结栏批次(编号,栏号,时刻,汇总数,报告) VALUES (?,?,?,?,?)""",
                (批, 栏号, 时刻, 0, json.dumps(报告, ensure_ascii=False)),
            )
            conn.execute("COMMIT")
            return 报告

        # —— 正路：契约内成片，确定性同票键 + 同事务消耗缓冲 ——
        汇总 = 0
        明细 = []
        for (站点, 指标), evs in groups.items():
            票 = KC.规范编号(栏号, 站点, 指标)
            for e in evs:
                conn.execute(
                    "UPDATE 入栏缓冲 SET 命运=? WHERE 报文编号=?",
                    ("已汇总", e["报文编号"]),
                )
                conn.execute(
                    "UPDATE 观测报文 SET 命运=? WHERE 报文编号=?",
                    ("已汇总", e["报文编号"]),
                )
            # 同票键唯一：重结/补结时回算该组全部缓冲并 UPSERT，绝不长出第二张现行票。
            agg = conn.execute(
                """SELECT COUNT(*) AS c, COALESCE(AVG(读数),0) AS a
                   FROM 入栏缓冲 WHERE 栏号=? AND 站点键=? AND 指标键=?""",
                (栏号, 站点, 指标),
            ).fetchone()
            总数 = int(agg["c"])
            总均 = float(agg["a"])
            conn.execute(
                """INSERT INTO 站点汇总账(汇总编号,栏号,站点键,指标键,报文数,均值,
                     物化时,命运,契约键,契约指纹,轨,裁决)
                   VALUES (?,?,?,?,?,?,?, '已汇总', ?, ?, '现行', '契约对齐')
                   ON CONFLICT(汇总编号) DO UPDATE SET
                     报文数=excluded.报文数, 均值=excluded.均值, 物化时=excluded.物化时,
                     契约键=excluded.契约键, 契约指纹=excluded.契约指纹,
                     轨='现行', 裁决='契约对齐'""",
                (票, 栏号, 站点, 指标, 总数, 总均, 时刻, 票, 现行指纹),
            )
            汇总 += 1
            明细.append({
                "汇总编号": 票,
                "契约键": 票,
                "契约指纹": 现行指纹,
                "站点键": 站点,
                "指标键": 指标,
                "本报文数": len(evs),
                "报文数": len(evs),
                "累计报文数": 总数,
                "均值": round(总均, 4),
            })
        报告 = {
            "批次编号": 批,
            "栏号": 栏号,
            "汇总数": 汇总,
            "缓冲消耗": len(rows),
            "契约指纹": 现行指纹,
            "契约形态": KC.形态,
            "明细": 明细,
            "空栏": len(rows) == 0,
        }
        conn.execute(
            """INSERT INTO 结栏批次(编号,栏号,时刻,汇总数,报告) VALUES (?,?,?,?,?)""",
            (批, 栏号, 时刻, 汇总, json.dumps(报告, ensure_ascii=False)),
        )
        conn.execute("COMMIT")
        return 报告
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        return {
            "批次编号": 批, "栏号": locals().get("栏号", ""),
            "汇总数": 0, "缓冲消耗": 0,
            "错误": "并发双键被契约唯一索引拦截", "并发冲突": True,
            "明细": [], "异常": str(e),
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def 回刷汇总(体: dict | list | None = None, **_) -> dict:
    """拒回刷闸门：逐条裁决拟回刷的汇总。

    同票且针在账册 -> 接收并 UPSERT 现行；错键/错针 -> 不进现行，写只读归档。
    """
    if isinstance(体, list):
        行们 = 体
    else:
        体 = 体 or {}
        行们 = 体.get("汇总") or 体.get("行") or 体.get("rows") or []
    批 = f"回刷-{uuid.uuid4().hex[:8]}"
    conn = 事务连接()
    接收: list[dict] = []
    拒收: list[dict] = []
    try:
        针 = KC.现行契约(conn)
        known = {r["契约指纹"] for r in conn.execute("SELECT 契约指纹 FROM 契约账册").fetchall()}
        for raw in 行们:
            r = dict(raw or {})
            栏号 = r.get("栏号") or _当前栏号(conn)
            站点 = r.get("站点键", "")
            指标 = r.get("指标键", "")
            编号 = r.get("汇总编号", "")
            票 = KC.规范编号(栏号, 站点, 指标)
            fp = r.get("契约指纹") or ""
            伪行 = {"栏号": 栏号, "站点键": 站点, "指标键": 指标,
                    "汇总编号": 编号, "契约指纹": fp, "报文数": r.get("报文数", 0)}
            v = KC.裁决行(伪行, 针, known)
            if v["裁决"] in KC.对齐裁决:
                conn.execute(
                    """INSERT INTO 站点汇总账(汇总编号,栏号,站点键,指标键,报文数,均值,
                         物化时,命运,契约键,契约指纹,轨,裁决)
                       VALUES (?,?,?,?,?,?,?, '已汇总', ?, ?, '现行', ?)
                       ON CONFLICT(汇总编号) DO UPDATE SET
                         报文数=excluded.报文数, 均值=excluded.均值,
                         契约指纹=excluded.契约指纹, 轨='现行', 裁决=excluded.裁决""",
                    (编号 or 票, 栏号, 站点, 指标, int(r.get("报文数", 0)),
                     float(r.get("均值", 0.0) or 0.0), 此刻(), 票,
                     fp or 针["契约指纹"], v["裁决"]),
                )
                接收.append({"汇总编号": 编号 or 票, "裁决": v["裁决"]})
            else:
                档号 = KC.写归档(
                    conn, 栏号=栏号, 站点键=站点, 指标键=指标, 汇总编号=编号,
                    来源批次=批, 契约指纹=fp, 报文数=r.get("报文数", 0),
                    均值=r.get("均值", 0.0), 裁决=v["裁决"], 载荷=r,
                )
                拒收.append({"归档号": 档号, "汇总编号": 编号,
                             "裁决": v["裁决"], "原因": v["原因"]})
        conn.execute("COMMIT")
        报告 = {
            "批次编号": 批, "接收数": len(接收), "拒收数": len(拒收),
            "接收": 接收, "拒收": 拒收,
        }
        if 拒收:
            报告["拒收"] = True
            报告["错误"] = f"{len(拒收)} 条错键汇总被拒回刷，已进只读归档"
        return 报告
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def 预览结栏() -> dict:
    """只读：当前栏在栏缓冲按站点指标分组，不写库；附契约票。"""
    from app.db import 连接
    c = 连接()
    try:
        规针 = KC.现行契约(c)
        r = c.execute("SELECT 值 FROM 栏规 WHERE 键='当前栏号'").fetchone()
        栏号 = r["值"] if r else "B-demo"
        rows = c.execute(
            "SELECT * FROM 入栏缓冲 WHERE 栏号=? AND 命运='在栏'", (栏号,)
        ).fetchall()
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for row in rows:
            groups[(row["站点键"], row["指标键"])].append(dict(row))
        预览 = []
        for (站点, 指标), evs in sorted(groups.items()):
            票 = KC.规范编号(栏号, 站点, 指标)
            预览.append({
                "站点键": 站点,
                "指标键": 指标,
                "契约键": 票,
                "汇总编号": 票,
                "契约指纹": 规针.get("契约指纹", KC.默认指纹),
                "报文数": len(evs),
                "均值": round(sum(float(e["读数"]) for e in evs) / len(evs), 4),
            })
        return {"栏号": 栏号, "在栏": len(rows),
                "契约指纹": 规针.get("契约指纹", KC.默认指纹),
                "将汇总组": 预览}
    finally:
        c.close()
