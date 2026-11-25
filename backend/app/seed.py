from datetime import datetime, timezone
from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 栏规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 站点源 (
  源键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  介质 TEXT NOT NULL DEFAULT '大气',
  区域 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 观测报文 (
  报文编号 TEXT PRIMARY KEY,
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已装载',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 入栏缓冲 (
  报文编号 TEXT PRIMARY KEY,
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  栏号 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '在栏',
  入栏时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 站点汇总账 (
  汇总编号 TEXT PRIMARY KEY,
  栏号 TEXT NOT NULL,
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  报文数 INTEGER NOT NULL,
  均值 REAL NOT NULL,
  物化时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已汇总',
  契约键 TEXT NOT NULL DEFAULT '',
  契约指纹 TEXT NOT NULL DEFAULT '',
  轨 TEXT NOT NULL DEFAULT '现行',
  裁决 TEXT NOT NULL DEFAULT '契约对齐'
);
CREATE TABLE IF NOT EXISTS 坏点匣 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  报文编号 TEXT NOT NULL,
  站点键 TEXT NOT NULL,
  原因 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  入匣时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 栏贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  在栏笔数 INTEGER NOT NULL,
  汇总报文数 INTEGER NOT NULL,
  坏点笔数 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL,
  说明 TEXT NOT NULL,
  契约指纹 TEXT NOT NULL DEFAULT '',
  契约对齐 INTEGER NOT NULL DEFAULT 1,
  契约汇总报文 INTEGER NOT NULL DEFAULT 0,
  错键报文 INTEGER NOT NULL DEFAULT 0,
  影子报文 INTEGER NOT NULL DEFAULT 0,
  键级报告 TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  入流 INTEGER NOT NULL DEFAULT 0,
  入栏 INTEGER NOT NULL DEFAULT 0,
  坏点 INTEGER NOT NULL DEFAULT 0,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 结栏批次 (
  编号 TEXT PRIMARY KEY,
  栏号 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  汇总数 INTEGER NOT NULL DEFAULT 0,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 契约账册 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  契约号 TEXT NOT NULL,
  形态 TEXT NOT NULL,
  契约指纹 TEXT NOT NULL UNIQUE,
  键字段 TEXT NOT NULL DEFAULT '[]',
  编号规则 TEXT NOT NULL DEFAULT '',
  策略 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '现行',
  时刻 TEXT NOT NULL,
  备注 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 错键归档 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  归档号 TEXT NOT NULL UNIQUE,
  来源批次 TEXT NOT NULL DEFAULT '',
  栏号 TEXT NOT NULL,
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  汇总编号 TEXT NOT NULL,
  契约键 TEXT NOT NULL DEFAULT '',
  契约指纹 TEXT NOT NULL DEFAULT '',
  应有契约键 TEXT NOT NULL DEFAULT '',
  应有指纹 TEXT NOT NULL DEFAULT '',
  报文数 INTEGER NOT NULL DEFAULT 0,
  均值 REAL NOT NULL DEFAULT 0,
  载荷 TEXT NOT NULL DEFAULT '{}',
  裁决 TEXT NOT NULL DEFAULT '错键',
  时刻 TEXT NOT NULL
);
"""

# 老库平滑加列（测试库永远是新库；这里照顾已挂载的 docker volume）。
_补列 = {
    "站点汇总账": [
        ("契约键", "TEXT NOT NULL DEFAULT ''"),
        ("契约指纹", "TEXT NOT NULL DEFAULT ''"),
        ("轨", "TEXT NOT NULL DEFAULT '现行'"),
        ("裁决", "TEXT NOT NULL DEFAULT '契约对齐'"),
    ],
    "栏贯验": [
        ("契约指纹", "TEXT NOT NULL DEFAULT ''"),
        ("契约对齐", "INTEGER NOT NULL DEFAULT 1"),
        ("契约汇总报文", "INTEGER NOT NULL DEFAULT 0"),
        ("错键报文", "INTEGER NOT NULL DEFAULT 0"),
        ("影子报文", "INTEGER NOT NULL DEFAULT 0"),
        ("键级报告", "TEXT NOT NULL DEFAULT '[]'"),
    ],
}


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _迁移(conn) -> None:
    for 表, 列们 in _补列.items():
        现有 = {r["name"] for r in conn.execute(f"PRAGMA table_info({表})").fetchall()}
        for 列, 型 in 列们:
            if 列 not in 现有:
                conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {型}")
    # 旧的随机尾巴编号行：回填契约键，并把非同票行挪出错键现行轨（不删账）。
    try:
        from app.services import 汇总键契约 as KC
        fp = KC.默认指纹
        rows = conn.execute("SELECT rowid AS rid, * FROM 站点汇总账").fetchall()
        for r in rows:
            票 = KC.规范编号(r["栏号"], r["站点键"], r["指标键"])
            同票 = r["汇总编号"] == 票
            if 同票:
                conn.execute(
                    """UPDATE 站点汇总账
                       SET 契约键=?, 契约指纹=CASE WHEN COALESCE(契约指纹,'')='' THEN ? ELSE 契约指纹 END,
                           轨='现行', 裁决='契约对齐'
                       WHERE rowid=?""",
                    (票, fp, r["rid"]),
                )
            else:
                conn.execute(
                    """UPDATE 站点汇总账
                       SET 契约键=?, 轨='错键', 裁决='错键'
                       WHERE rowid=?""",
                    (票, r["rid"]),
                )
        # 同组若历史上已双键，仅留最新一行在现行轨，其余转重复键，保证唯一索引可建。
        dups = conn.execute(
            """SELECT 栏号,站点键,指标键, MAX(rowid) AS keep
               FROM 站点汇总账 WHERE 轨='现行'
               GROUP BY 栏号,站点键,指标键 HAVING COUNT(*)>1"""
        ).fetchall()
        for d in dups:
            conn.execute(
                """UPDATE 站点汇总账 SET 轨='重复键', 裁决='重复键'
                   WHERE 轨='现行' AND 栏号=? AND 站点键=? AND 指标键=? AND rowid<>?""",
                (d["栏号"], d["站点键"], d["指标键"], d["keep"]),
            )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_现行契约票
               ON 站点汇总账(栏号,站点键,指标键) WHERE 轨='现行'"""
        )
    except Exception:
        # 迁移兜底不应挡住启动；新库不受影响。
        pass


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _迁移(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 栏规").fetchone()["c"] == 0:
        for k, v in {
            "当前栏号": "B-demo",
            "PM25上限": "75",
            "PM25下限": "0",
            "标定偏移上限": "5",
            "栏策略": "按站点指标汇总均值",
        }.items():
            conn.execute("INSERT INTO 栏规(键,值) VALUES (?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 站点源").fetchone()["c"] == 0:
        for row in [
            ("站-河东", "河东大气站", "大气", "城区", "示范站"),
            ("站-湖西", "湖西水质站", "水质", "湖区", "示范站"),
            ("站-北岭", "北岭大气站", "大气", "近郊", "示范站"),
            ("站-江口", "江口大气站", "大气", "河口", "示范站"),
            ("站-林场", "林场微站", "大气", "林区", "示范站"),
        ]:
            conn.execute(
                "INSERT INTO 站点源(源键,名称,介质,区域,说明) VALUES (?,?,?,?,?)",
                row,
            )
    if conn.execute("SELECT COUNT(*) AS c FROM 契约账册").fetchone()["c"] == 0:
        from app.services import 汇总键契约 as KC
        d = KC.契约定义()
        conn.execute(
            """INSERT INTO 契约账册(契约号,形态,契约指纹,键字段,编号规则,策略,状态,时刻,备注)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                d["版本"], d["形态"], KC.默认指纹, json_dumps(d["键字段"]),
                d["编号规则"], d["策略"], "现行", 此刻(), "开库默认钉针",
            ),
        )
    conn.commit()
    conn.close()


def json_dumps(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False)
