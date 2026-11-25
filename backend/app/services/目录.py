from pathlib import Path
from app.db import 连接, 事务连接
from app.services import 校验, 汇总键契约 as KC


def 栏规() -> dict:
    conn = 连接()
    try:
        规 = {r["键"]: r["值"] for r in conn.execute("SELECT 键,值 FROM 栏规").fetchall()}
        try:
            针 = KC.现行契约(conn)
            fp = 针.get("契约指纹", KC.默认指纹)
            规.update({
                "汇总键契约": f"{KC.形态}｜{KC.策略名}",
                "契约形态": KC.形态,
                "契约指纹": fp,
                "契约版本": 针.get("契约号", KC.契约版本),
                "契约键字段": "栏号 + 站点键 + 指标键",
                "契约编号规则": KC.编号模板,
                "契约键示例": KC.规范编号(规.get("当前栏号", "B-demo"), "站-河东", "PM25"),
            })
        except Exception:
            pass
        return 规
    finally:
        conn.close()


def 改栏规(体: dict) -> dict:
    错 = 校验.校验栏规补丁(体 or {})
    if 错:
        return {"错误": 校验.摘要错误(错), "栏规": 栏规()}
    conn = 事务连接()
    try:
        for k, v in (体 or {}).items():
            conn.execute(
                "INSERT INTO 栏规(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (str(k), str(v)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return 栏规()


def 站点列表() -> list[dict]:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 站点源 ORDER BY 源键").fetchall()]
    finally:
        conn.close()


def 报文列表(限制: int = 200) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 观测报文 ORDER BY 观测时 DESC LIMIT ?", (限制,)
            ).fetchall()
        ]
    finally:
        conn.close()


def 缓冲列表(命运: str | None = None) -> list[dict]:
    conn = 连接()
    try:
        针 = KC.现行契约(conn)
        fp = 针.get("契约指纹", KC.默认指纹)
        if 命运:
            rows = conn.execute(
                "SELECT * FROM 入栏缓冲 WHERE 命运=? ORDER BY 观测时", (命运,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM 入栏缓冲 ORDER BY 观测时").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 每张缓冲卡片都带得出它将要物化的那枚契约票
            d["契约键"] = KC.规范编号(d["栏号"], d["站点键"], d["指标键"])
            d["汇总编号"] = d["契约键"]
            d["契约指纹"] = fp
            out.append(d)
        return out
    finally:
        conn.close()


def 汇总列表(限制: int = 200, 现行_only: bool = True, 全量: bool | None = None) -> list[dict]:
    """站点汇总账读模型。

    默认只读“现行契约键集”：读时按汇总键契约裁决，错键/错针行不进现行列表，
    只能通过 汇总键裁决 / 错键归档 看到。全量=True（或 现行_only=False）返回所有正式行。
    """
    if 全量 is True:
        现行_only = False
    conn = 连接()
    try:
        针 = KC.现行契约(conn)
        known = {r["契约指纹"] for r in conn.execute("SELECT 契约指纹 FROM 契约账册").fetchall()}
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 站点汇总账 ORDER BY 物化时 DESC LIMIT ?", (限制,)
            ).fetchall()
        ]
        out = []
        for r in rows:
            v = KC.裁决行(r, 针, known)
            r["契约键"] = v["契约键"]
            r["裁决"] = v["裁决"]
            r["现行可回读契约"] = v["现行可回读契约"]
            r.setdefault("契约指纹", v["契约指纹"])
            if 现行_only and v["裁决"] not in KC.对齐裁决:
                continue
            out.append(r)
        return out
    finally:
        conn.close()


def 汇总全列表(限制: int = 200) -> list[dict]:
    return 汇总列表(限制=限制, 现行_only=False)


def 汇总键裁决(栏号: str | None = None, 站点键: str | None = None,
               指标键: str | None = None, 键: str | None = None) -> dict:
    return KC.裁决全库(栏号=栏号, 站点键=站点键, 指标键=指标键, 键=键)


# 只读裁决接口的别名
键裁决 = 汇总键裁决
裁决 = 汇总键裁决


def 汇总键契约() -> dict:
    conn = 连接()
    try:
        d = KC.describe()
        d["现行针"] = KC.现行契约(conn)
        d["账册"] = KC.账册列表()
        return d
    finally:
        conn.close()


def 契约账册列表(限制: int = 50) -> list[dict]:
    return KC.账册列表(限制)


def 错键归档列表(限制: int = 100) -> list[dict]:
    return KC.归档列表(限制)


# 影子轨别名
影子列表 = 错键归档列表
归档列表 = 错键归档列表


def 契约切流(体: dict | None = None) -> dict:
    return KC.切流(体 or {})


# 切换入口的若干近义名，便于不同调用面发现
切换契约 = 契约切流
汇总键契约切流 = 契约切流
切流 = 契约切流


def 坏点列表(限制: int = 200) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 坏点匣 ORDER BY 编号 DESC LIMIT ?", (限制,)
            ).fetchall()
        ]
    finally:
        conn.close()


def 贯验列表(限制: int = 50) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 栏贯验 ORDER BY 编号 DESC LIMIT ?", (限制,)
            ).fetchall()
        ]
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        return {
            "观测报文": conn.execute("SELECT COUNT(*) AS c FROM 观测报文").fetchone()["c"],
            "在栏": conn.execute("SELECT COUNT(*) AS c FROM 入栏缓冲 WHERE 命运='在栏'").fetchone()["c"],
            "站点汇总": conn.execute("SELECT COUNT(*) AS c FROM 站点汇总账").fetchone()["c"],
            "坏点": conn.execute("SELECT COUNT(*) AS c FROM 坏点匣").fetchone()["c"],
            "栏规": 栏规(),
        }
    finally:
        conn.close()


def 装载批次列表(限制: int = 50) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT 编号,时刻,入流,入栏,坏点 FROM 装载批次 ORDER BY 时刻 DESC LIMIT ?",
                (限制,),
            ).fetchall()
        ]
    finally:
        conn.close()


def 结栏批次列表(限制: int = 50) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT 编号,栏号,时刻,汇总数 FROM 结栏批次 ORDER BY 时刻 DESC LIMIT ?",
                (限制,),
            ).fetchall()
        ]
    finally:
        conn.close()


def 站点查(源键: str) -> dict | None:
    conn = 连接()
    try:
        r = conn.execute("SELECT * FROM 站点源 WHERE 源键=?", (源键,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()
