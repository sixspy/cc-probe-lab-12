#!/usr/bin/env python3
"""并发双请求脚本：契约切换 ⇄ 结栏 同时打，验证现行轨绝不双键。

用法：
    python 并发双请求.py                 # 自动探测 8100 / 8210
    BASE_URL=http://127.0.0.1:8210 python 并发双请求.py --rounds 10
    python 并发双请求.py --base http://127.0.0.1:8100 --double-close

每轮：
  1) 切一个独立栏号并灌入契约内成片（唯一报文号）；
  2) 栅栏对齐两个线程，同一瞬间发出「结栏」与「契约切流」两个 POST
     （--double-close 时改为两个结栏请求）；
  3) 拉全量汇总账，按 (栏号,站点,指标) 数现行契约行，>1 即双键，脚本退 1。

仅用标准库，便于在装了 docker compose 的宿主直接跑。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter


def _req(base: str, method: str, path: str, body: dict | None = None, timeout: float = 40):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    # urllib 不会自动给中文路径百分号编码，需显式 quote（保留 / ? = & ）。
    url = base + urllib.parse.quote(path, safe="/?=&%+")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"原文": raw}


def _probe_base(cli_base: str | None) -> str:
    candidates = [cli_base, os.environ.get("BASE_URL"), "http://127.0.0.1:8100",
                  "http://127.0.0.1:8210"]
    for b in candidates:
        if not b:
            continue
        try:
            st, _ = _req(b, "GET", "/api/空桩", timeout=3)
            if st == 200:
                return b.rstrip("/")
        except Exception:
            continue
    raise SystemExit("探不到探栏服务，请先 docker compose up 或 hypercorn 起在 8100/8210，"
                     "并用 --base / BASE_URL 指定。")


def _load_batch(round_i: int, stamp: str):
    return {
        "批次编号": f"并发脚本-{stamp}-{round_i}",
        "报文": [
            {"报文编号": f"J-{stamp}-{round_i}-{k}", "站点键": "站-河东", "指标键": "PM25",
             "读数": 20 + k, "观测时": "2026-11-24T08:10:00Z"}
            for k in range(3)
        ] + [
            {"报文编号": f"J-{stamp}-{round_i}-b", "站点键": "站-北岭", "指标键": "PM25",
             "读数": 28.0, "观测时": "2026-11-24T08:20:00Z"}
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--double-close", action="store_true",
                    help="并发双结栏（默认是 结栏⇄切流 双请求）")
    args = ap.parse_args()
    base = _probe_base(args.base)
    print(f"[base] {base}  rounds={args.rounds}  double_close={args.double_close}")

    stamp = uuid.uuid4().hex[:8]
    failures: list[str] = []

    for i in range(args.rounds):
        pen = f"B-jb-{stamp}-{i}"
        st, g = _req(base, "POST", "/api/栏规", {"当前栏号": pen})
        if st != 200 or "错误" in g:
            failures.append(f"r{i} 切栏号失败 {st} {g}")
            continue
        st, ld = _req(base, "POST", "/api/观测报文/装载", _load_batch(i, stamp))
        if st != 200 or ld.get("入栏") != 4:
            failures.append(f"r{i} 装载异常 {st} {ld}")
            continue

        gate = threading.Barrier(2)
        out: dict[str, object] = {}

        def close():
            gate.wait()
            out["close"] = _req(base, "POST", "/api/结栏/跑", {})

        def other():
            gate.wait()
            if args.double_close:
                out["other"] = _req(base, "POST", "/api/结栏/跑", {})
            else:
                out["other"] = _req(base, "POST", "/api/汇总键契约/切流",
                                    {"备注": f"脚本并发{i}"})

        t1 = threading.Thread(target=close)
        t2 = threading.Thread(target=other)
        t1.start(); t2.start(); t1.join(timeout=60); t2.join(timeout=60)

        st_c, rc = out.get("close", (0, {}))  # type: ignore[assignment]
        if st_c != 200 or (isinstance(rc, dict) and rc.get("错误") and not rc.get("并发冲突")):
            # 并发冲突结构化回执也算保护生效，但正常应被锁串行成 空栏/成功。
            failures.append(f"r{i} 结栏异常 {st_c} {rc}")

        # 账册核验：该栏每个 (站点,指标) 至多一枚现行契约行
        st, rows = _req(base, "GET", "/api/站点汇总账?全量=1")
        if st != 200 or not isinstance(rows, list):
            failures.append(f"r{i} 拉汇总账失败 {st}")
            continue
        c = Counter()
        for r in rows:
            if r.get("栏号") != pen:
                continue
            if r.get("裁决") in ("契约对齐", "历史契约"):
                c[(r["站点键"], r["指标键"])] += 1
        dup = {k: n for k, n in c.items() if n > 1}
        if dup:
            failures.append(f"r{i} 双键现行 {dup}")
            print(f"  ✗ {pen} 双键 {dup}")
        else:
            print(f"  ✓ r{i} {pen} 现行键 {dict(c)} 无双键")
        time.sleep(0.05)

    # 账册任意时刻仅一枚现行针
    st, ledger = _req(base, "GET", "/api/契约账册")
    active = [z for z in (ledger.get("账册") if isinstance(ledger, dict) else []) or []
              if z.get("状态") == "现行"]
    if len(active) != 1:
        failures.append(f"契约账册现行针数={len(active)}（应为 1）")

    if failures:
        print("\n[红] 并发不变量被破坏：")
        for f in failures:
            print(" -", f)
        return 1
    print(f"\n[绿] {args.rounds} 轮并发双请求均无双键现行，账册现行针唯一。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
