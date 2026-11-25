from pathlib import Path
import asyncio
from quart import Quart, jsonify, request, send_from_directory, redirect
from app import seed
from app.services import 目录, 装载, 结栏, 贯验, 夹具, 统计, 说明, 汇总键契约

_STATIC = Path(__file__).resolve().parent / "static"
app = Quart(__name__)


@app.before_serving
async def _boot():
    seed.初始化库()


@app.get("/login")
async def login_page():
    return await send_from_directory(_STATIC, "login.html")


@app.get("/")
async def index():
    return await send_from_directory(_STATIC, "index.html")


@app.get("/api/概览")
async def api_概览():
    return jsonify(目录.概览())


@app.get("/api/仓情")
async def api_仓情():
    return jsonify(说明.仓情())


@app.get("/api/栏规")
async def api_栏规_get():
    return jsonify(目录.栏规())


@app.post("/api/栏规")
async def api_栏规_post():
    body = await request.get_json(force=True, silent=True) or {}
    return jsonify(目录.改栏规(body))


@app.get("/api/站点源")
async def api_站点():
    return jsonify(目录.站点列表())


@app.get("/api/观测报文")
async def api_报文():
    return jsonify(目录.报文列表())


@app.get("/api/入栏缓冲")
async def api_缓冲():
    return jsonify(目录.缓冲列表())


@app.get("/api/站点汇总账")
async def api_汇总():
    全量 = request.args.get("全量") in ("1", "true", "True", "是")
    return jsonify(目录.汇总列表(全量=全量))


@app.get("/api/汇总键裁决")
async def api_汇总键裁决():
    return jsonify(目录.汇总键裁决(
        栏号=request.args.get("栏号") or None,
        站点键=request.args.get("站点键") or None,
        指标键=request.args.get("指标键") or None,
        键=request.args.get("键") or None,
    ))


@app.get("/api/汇总键裁决只读")
async def api_汇总键裁决别名():
    return jsonify(目录.汇总键裁决(
        栏号=request.args.get("栏号") or None,
        站点键=request.args.get("站点键") or None,
        指标键=request.args.get("指标键") or None,
    ))


@app.get("/api/汇总键契约")
async def api_汇总键契约():
    return jsonify(目录.汇总键契约())


@app.get("/api/契约账册")
async def api_契约账册():
    return jsonify({"账册": 目录.契约账册列表(), "现行": 汇总键契约.现行契约()})


@app.get("/api/错键归档")
async def api_错键归档():
    return jsonify(目录.错键归档列表())


@app.post("/api/汇总键契约/切流")
async def api_契约切流():
    body = await request.get_json(force=True, silent=True) or {}
    return jsonify(await asyncio.to_thread(目录.契约切流, body))


@app.post("/api/汇总/回刷")
async def api_汇总回刷():
    body = await request.get_json(force=True, silent=True) or {}
    return jsonify(await asyncio.to_thread(结栏.回刷汇总, body))


@app.get("/api/坏点匣")
async def api_坏点():
    return jsonify(目录.坏点列表())


@app.get("/api/栏贯验")
async def api_贯验列表():
    return jsonify(目录.贯验列表())


@app.get("/api/夹具")
async def api_夹具():
    return jsonify(夹具.列表())


@app.post("/api/观测报文/装载")
async def api_装载():
    body = await request.get_json(force=True, silent=True) or {}
    return jsonify(装载.装一批(body))


@app.post("/api/观测报文/夹具/<name>")
async def api_夹具装载(name: str):
    try:
        return jsonify(装载.装一批(夹具.读取(name)))
    except FileNotFoundError:
        return jsonify({"错误": name}), 404


@app.post("/api/结栏/跑")
async def api_结栏():
    body = await request.get_json(force=True, silent=True) or {}
    # 放到线程：并发双请求会拿到两枚 sqlite 连接真实争用 BEGIN IMMEDIATE 锁。
    return jsonify(await asyncio.to_thread(结栏.结当前栏, body))


@app.get("/api/结栏/预览")
async def api_结栏预览():
    return jsonify(结栏.预览结栏())


@app.get("/api/栏贯验/快照")
async def api_贯验快照():
    return jsonify(贯验.只读快照())


@app.get("/api/夹具/预检")
async def api_夹具预检():
    return jsonify(夹具.全部预检())


@app.post("/api/栏贯验/跑")
async def api_贯验():
    return jsonify(贯验.跑一次())


@app.get("/api/统计/报文命运")
async def api_统计报文():
    return jsonify(统计.报文命运())


@app.get("/api/统计/缓冲命运")
async def api_统计缓冲():
    return jsonify(统计.缓冲命运())




@app.get("/api/装载批次")
async def api_装载批次():
    return jsonify(目录.装载批次列表())


@app.get("/api/结栏批次")
async def api_结栏批次():
    return jsonify(目录.结栏批次列表())


@app.get("/api/统计/按站点")
async def api_按站点():
    return jsonify(统计.按站点报文())


@app.get("/api/统计/按指标")
async def api_按指标():
    return jsonify(统计.按指标汇总())


@app.get("/api/统计/装载摘要")
async def api_装载摘要():
    return jsonify(统计.装载批次摘要())


@app.get("/api/统计/结栏摘要")
async def api_结栏摘要():
    return jsonify(统计.结栏批次摘要())

@app.get("/api/空桩")
async def api_空桩():
    root = Path(__file__).resolve().parent / "modules"
    out = []
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if p.is_dir() and p.name != "__pycache__" and (p / "__init__.py").is_file():
                out.append(p.name)
    return jsonify({"空桩": out, "条数": len(out)})


@app.get("/<path:name>")
async def static_page(name: str):
    p = _STATIC / name
    if not p.is_file():
        return redirect("/")
    return await send_from_directory(_STATIC, name)
