"""汇总键契约（由空桩落地为真实编排）。

契约：汇总编号必须含栏号+站点+指标，且汇总编号与分组键同票：
    汇总编号 == 契约键 == 汇-<栏号>-<站点键>-<指标键>

工程形态（甲案·切流账册）：
- 契约账册钉唯一现行契约指纹；
- 写汇总键与缓冲消耗在同一 BEGIN IMMEDIATE 事务同成败；
- 切流与结栏并发由写锁串行，库内部分唯一索引兜底双键；
- 错键/错针汇总拒入现行、留痕只读错键归档；
- 新增只读裁决：GET /api/汇总键裁决。

本模块保留原空桩的 NAME/CONTRACT/HOOKS/describe 表面，并把真实实现 re-export，
使 ``from app.modules.汇总键契约 import 裁决全库`` 与
``from app.services.汇总键契约 import ...`` 两条路都可达。
"""
from __future__ import annotations

from app.services.汇总键契约 import (  # noqa: F401
    契约名 as NAME,
    形态 as CONTRACT_FORM,
    键字段 as HOOK_KEYS,
    编号模板 as ID_RULE,
    默认指纹,
    契约定义,
    规范编号,
    契约键of,
    分组串,
    指纹of,
    现行契约,
    已知指纹集,
    裁决行,
    裁决全库,
    裁决,
    汇总键裁决,
    裁决只读,
    切流,
    账册列表,
    归档列表,
    写归档,
    describe as _describe,
)

CONTRACT = "汇总编号必须含栏号+站点+指标，且与分组键同票；账册钉契约指纹"
HOOKS = ("before_close", "materialize_key", "adjudicate", "extra_verify_checks")


def describe() -> dict:
    d = _describe()
    d["空桩"] = NAME
    d["契约"] = CONTRACT
    d["钩子"] = list(HOOKS)
    d["默认启用"] = True
    return d


def before_close(pen_id: str) -> None:
    # 现行实现把针读进结栏事务本身；此钩子仅保留扩展面。
    return None


def extra_verify_checks(snapshot: dict) -> list[str]:
    """供三联栏贯验复用的额外键契约检查。"""
    问题: list[str] = []
    for k in snapshot.get("键级", []) or []:
        if k.get("裁决") not in ("契约对齐",):
            问题.append(f"{k.get('契约键')} 键契约裁决={k.get('裁决')}")
    return 问题
