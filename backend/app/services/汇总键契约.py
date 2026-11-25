"""汇总键契约：物化汇总的键形态、契约指纹账册与只读裁决。

择定的工程形态（甲案：切流账册）
--------------------------------
- 契约形态钉死为「栏号 + 站点键 + 指标键」，汇总编号与分组键同票：
      汇总编号 == 契约键 == 汇-<栏号>-<站点键>-<指标键>
- ``契约账册`` 是切流账册，任意时刻恰好一枚 ``状态='现行'`` 的契约指纹。
- 结栏在同一枚 ``BEGIN IMMEDIATE`` 写事务里读针、分组、写现行汇总键、消耗缓冲，
  因此写键与缓冲消耗同成败，切流与结栏并发被串行化；库内另有部分唯一索引兜底双键。
- 持错指纹/错键的回刷与结栏被拒于现行轨之外，错键载荷留痕到只读 ``错键归档``
  （裁决影子轨），贯验永不把影子计入契约对齐口径。

乙案的可观测不变量（现行读模型无错键、归档有错键）也由“拒收 + 留痕归档”同时满足，
但它不是双写物化：拒收时缓冲不消耗。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid

from app.db import 连接
from app.seed import 此刻

契约名 = "汇总键契约"
契约版本 = "sumkey-v1"
形态 = "栏号+站点+指标"
键字段 = ("栏号", "站点键", "指标键")
编号模板 = "汇-{栏号}-{站点键}-{指标键}"
策略名 = "切流账册·钉契约指纹·拒回刷错键汇总"

# 可作为现行契约对齐的行裁决（历史账册指纹仍可回读契约键集）。
对齐裁决 = ("契约对齐", "历史契约")
非现行裁决 = ("错键", "指纹不符", "未钉指纹", "重复键")


def 契约定义() -> dict:
    return {
        "契约名": 契约名,
        "版本": 契约版本,
        "形态": 形态,
        "键字段": list(键字段),
        "编号规则": 编号模板,
        "策略": 策略名,
        "默认启用": True,
    }


def 规范编号(栏号: str, 站点键: str, 指标键: str) -> str:
    """汇总编号与分组键同票的唯一合法形态。"""
    return f"汇-{栏号}-{站点键}-{指标键}"


# 别名：不同读面把这枚票叫作 契约键 / 裁决键。
契约键of = 规范编号
裁决键 = 规范编号


def 分组串(栏号: str, 站点键: str, 指标键: str) -> str:
    return f"{栏号}|{站点键}|{指标键}"


def 分组字典(栏号: str, 站点键: str, 指标键: str) -> dict:
    return {"栏号": 栏号, "站点键": 站点键, "指标键": 指标键}


def 指纹of(定义: dict | None = None) -> str:
    d = 定义 or {
        "版本": 契约版本,
        "形态": 形态,
        "键字段": list(键字段),
        "编号规则": 编号模板,
    }
    blob = json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fp-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


默认指纹 = 指纹of()


def 现行契约(conn=None) -> dict:
    """读当前钉住的契约针；可传入调用方事务连接以保证同事务一致读。"""
    own = conn is None
    c = conn or 连接()
    try:
        try:
            r = c.execute(
                "SELECT * FROM 契约账册 WHERE 状态='现行' ORDER BY 编号 DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            r = None
        if r is not None:
            d = dict(r)
            try:
                d["键字段"] = json.loads(d.get("键字段") or "[]") or list(键字段)
            except (TypeError, ValueError):
                d["键字段"] = list(键字段)
            return d
        d = 契约定义()
        d.update({"契约号": 契约版本, "契约指纹": 默认指纹,
                  "状态": "现行", "时刻": None, "备注": "内置默认针"})
        return d
    finally:
        if own:
            c.close()


def 已知指纹集(conn=None) -> set[str]:
    own = conn is None
    c = conn or 连接()
    try:
        try:
            return {r["契约指纹"] for r in c.execute("SELECT 契约指纹 FROM 契约账册").fetchall()}
        except sqlite3.OperationalError:
            return {默认指纹}
    finally:
        if own:
            c.close()


def 裁决行(行: dict, 现行针: dict | None = None, 已知指纹: set[str] | None = None) -> dict:
    """对一条站点汇总账行做键契约裁决（读时重算，不信存储的裁决列）。"""
    栏号 = 行.get("栏号", "")
    站点 = 行.get("站点键", "")
    指标 = 行.get("指标键", "")
    编号 = 行.get("汇总编号", "")
    票 = 规范编号(栏号, 站点, 指标)
    fp = 行.get("契约指纹") or ""
    active = (现行针 or {}).get("契约指纹") or 默认指纹
    known = set(已知指纹 if 已知指纹 is not None else {默认指纹})
    known.add(active)

    结构对 = 编号 == 票
    if not 结构对:
         verdict, 原因 = "错键", "汇总编号未与栏号+站点+指标同票（疑似随机尾巴/回刷错键）"
    elif not fp:
        verdict, 原因 = "未钉指纹", "同票但未钉契约指纹，无法证明出自切流账册"
    elif fp not in known:
        verdict, 原因 = "指纹不符", "契约指纹不在切流账册（错针/旧针回刷）"
    elif fp != active:
        verdict, 原因 = "历史契约", "同票且指纹属历史账册，键集可回读"
    else:
        verdict, 原因 = "契约对齐", "汇总编号=契约键，且钉住现行契约指纹"

    return {
        "汇总编号": 编号,
        "栏号": 栏号,
        "站点键": 站点,
        "指标键": 指标,
        "契约键": 票,
        "分组键": 分组串(栏号, 站点, 指标),
        "契约指纹": fp,
        "现行指纹": active,
        "裁决": verdict,
        "原因": 原因,
        "现行可回读契约": verdict in 对齐裁决,
        "契约回读键": 票 if verdict in 对齐裁决 else None,
        "报文数": 行.get("报文数", 0),
        "均值": 行.get("均值", 0),
        "物化时": 行.get("物化时"),
        "轨": 行.get("轨", "现行"),
    }


def _归档项(r) -> dict:
    d = dict(r)
    d.setdefault("裁决", "错键")
    d["现行可回读契约"] = False
    d["契约回读键"] = None
    return d


def 裁决全库(栏号: str | None = None, 站点键: str | None = None,
             指标键: str | None = None, 键: str | None = None) -> dict:
    """只读：汇总键裁决。返回现行契约、现行键集、错键正式行、影子归档与逐键回读。"""
    conn = 连接()
    try:
        针 = 现行契约(conn)
        known = {r["契约指纹"] for r in conn.execute("SELECT 契约指纹 FROM 契约账册").fetchall()}
        sql = "SELECT * FROM 站点汇总账"
        args: list = []
        cond = []
        if 栏号:
            cond.append("栏号=?"); args.append(栏号)
        if 站点键:
            cond.append("站点键=?"); args.append(站点键)
        if 指标键:
            cond.append("指标键=?"); args.append(指标键)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]

        现行: list[dict] = []
        错键: list[dict] = []
        for r in rows:
            item = 裁决行(r, 针, known)
            (现行 if item["裁决"] in 对齐裁决 else 错键).append(item)

        asql = "SELECT * FROM 错键归档"
        aargs: list = []
        acond = []
        if 栏号:
            acond.append("栏号=?"); aargs.append(栏号)
        if 站点键:
            acond.append("站点键=?"); aargs.append(站点键)
        if 指标键:
            acond.append("指标键=?"); aargs.append(指标键)
        if acond:
            asql += " WHERE " + " AND ".join(acond)
        asql += " ORDER BY 编号 DESC"
        影子 = [_归档项(r) for r in conn.execute(asql, aargs).fetchall()]

        # 逐契约键的回读视图：并上影子里出现过的键，保证“错键结栏后可否回读”可区分。
        键视图: dict[str, dict] = {}

        def _槽(pen, st, me):
            k = 分组串(pen, st, me)
            return 键视图.setdefault(k, {
                "栏号": pen, "站点键": st, "指标键": me,
                "契约键": 规范编号(pen, st, me), "分组键": k,
                "现行可回读契约": False, "契约回读键": None,
                "裁决": "缺失", "现行": None, "影子数": 0, "错键正式行数": 0,
            })

        for it in 现行:
            s = _槽(it["栏号"], it["站点键"], it["指标键"])
            s["现行可回读契约"] = True
            s["契约回读键"] = it["契约键"]
            s["裁决"] = "契约对齐" if it["裁决"] == "契约对齐" else it["裁决"]
            s["现行"] = it
        for it in 错键:
            s = _槽(it["栏号"], it["站点键"], it["指标键"])
            s["错键正式行数"] += 1
            if s["裁决"] == "缺失":
                s["裁决"] = it["裁决"]
        for sh in 影子:
            s = _槽(sh["栏号"], sh["站点键"], sh["指标键"])
            s["影子数"] += 1
            if not s["现行可回读契约"] and s["裁决"] == "缺失":
                s["裁决"] = "仅错键影子"

        if 键:
            键视图 = {k: v for k, v in 键视图.items()
                      if 键 in (k, v["契约键"], v["现行"] and v["现行"]["汇总编号"])}

        现行键集 = sorted(it["契约键"] for it in 现行)
        现行干净 = not 错键
        if 现行干净 and not 影子:
            结论 = "契约对齐"
        elif 现行干净:
            结论 = "现行对齐·存在错键影子"
        else:
            结论 = "现行含错键"

        return {
            "接口": "汇总键裁决",
            "只读": True,
            "契约": {
                "契约名": 契约名,
                "形态": 形态,
                "版本": 针.get("契约号", 契约版本),
                "契约指纹": 针.get("契约指纹", 默认指纹),
                "键字段": 针.get("键字段", list(键字段)),
                "编号规则": 针.get("编号规则", 编号模板),
                "策略": 针.get("策略", 策略名),
                "契约键示例": 规范编号(针 and 针.get("栏号") or "B-demo", "站-河东", "PM25"),
            },
            "对齐": 现行干净,
            "结论": 结论,
            "现行键集": 现行键集,
            "现行": 现行,
            "错键": 错键,
            "影子": 影子,
            "键视图": sorted(键视图.values(), key=lambda x: x["分组键"]),
            "计数": {
                "现行": len(现行),
                "错键正式行": len(错键),
                "影子归档": len(影子),
            },
        }
    finally:
        conn.close()


# 只读接口的别名，方便不同调用面发现。
裁决 = 裁决全库
汇总键裁决 = 裁决全库
裁决只读 = 裁决全库


def 写归档(conn, *, 栏号, 站点键, 指标键, 汇总编号, 来源批次="",
           契约指纹="", 报文数=0, 均值=0.0, 裁决="错键", 载荷=None,
           应有指纹: str | None = None) -> str:
    归档号 = f"档-{uuid.uuid4().hex[:10]}"
    票 = 规范编号(栏号, 站点键, 指标键)
    针 = 现行契约(conn)
    conn.execute(
        """INSERT INTO 错键归档(归档号,来源批次,栏号,站点键,指标键,汇总编号,契约键,
             契约指纹,应有契约键,应有指纹,报文数,均值,载荷,裁决,时刻)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            归档号, 来源批次, 栏号, 站点键, 指标键, 汇总编号,
            票 if 汇总编号 == 票 else 汇总编号,
            str(契约指纹 or ""), 票, 应有指纹 or 针.get("契约指纹", 默认指纹),
            int(报文数 or 0), float(均值 or 0.0),
            json.dumps(载荷 or {}, ensure_ascii=False, default=str),
            裁决, 此刻(),
        ),
    )
    return 归档号


def 切流(体: dict | None = None) -> dict:
    """切换/重钉契约指纹。与结栏共用 BEGIN IMMEDIATE 写锁，串行化竞态。"""
    体 = 体 or {}
    # 延迟导入避免 db/seed 之外的环。
    from app.db import 事务连接
    conn = 事务连接()
    try:
        旧 = 现行契约(conn)
        显式针 = 体.get("契约指纹") or 体.get("指纹") or 体.get("新指纹")
        新针 = str(显式针) if 显式针 else "pin-" + uuid.uuid4().hex[:16]
        # 并发下若显式针撞唯一约束，交给上层兜底；随机针不会撞。
        conn.execute("UPDATE 契约账册 SET 状态='历史' WHERE 状态='现行'")
        号 = str(体.get("契约号") or 体.get("版本") or 契约版本)
        形态新 = str(体.get("形态") or 形态)
        规则新 = str(体.get("编号规则") or 编号模板)
        策略新 = str(体.get("策略") or 策略名)
        备注 = str(体.get("备注") or "契约切流")
        conn.execute(
            """INSERT INTO 契约账册(契约号,形态,契约指纹,键字段,编号规则,策略,状态,时刻,备注)
               VALUES (?,?,?,?,?,?, '现行', ?,?)""",
            (号, 形态新, 新针, json.dumps(list(键字段), ensure_ascii=False),
             规则新, 策略新, 此刻(), 备注),
        )
        conn.execute("COMMIT")
        return {
            "切流": True,
            "旧指纹": 旧.get("契约指纹", 默认指纹),
            "契约指纹": 新针,
            "形态": 形态新,
            "编号规则": 规则新,
            "时刻": 此刻(),
            "账册": "契约账册",
        }
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        return {"错误": "契约指纹重复，切流未生效", "并发冲突": True, "明细": str(e)}
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def 账册列表(限制: int = 50) -> list[dict]:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 契约账册 ORDER BY 编号 DESC LIMIT ?", (限制,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["键字段"] = json.loads(d.get("键字段") or "[]")
            except (TypeError, ValueError):
                d["键字段"] = []
            out.append(d)
        return out
    finally:
        conn.close()


def 归档列表(限制: int = 100) -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 错键归档 ORDER BY 编号 DESC LIMIT ?", (限制,)
        ).fetchall()]
    finally:
        conn.close()


def describe() -> dict:
    d = 契约定义()
    d.update({
        "空桩": 契约名,
        "契约": "汇总编号必须含栏号+站点+指标，且与分组键同票；账册钉契约指纹",
        "钩子": ("before_close", "materialize_key", "adjudicate", "extra_verify_checks"),
        "接口": ["GET /api/汇总键裁决", "GET /api/汇总键契约",
                 "POST /api/汇总键契约/切流", "GET /api/错键归档"],
    })
    return d
