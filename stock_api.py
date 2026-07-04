"""
stock_api.py — 股票实时数据 API 服务
基于 AKShare + FastAPI，为智谱清言 Agent 提供股票数据接口

启动命令：uvicorn stock_api:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import akshare as ak
import pandas as pd
import json
from datetime import datetime, timedelta
from functools import lru_cache
import time

app = FastAPI(
    title="股票实时数据 API",
    description="为智谱清言 Agent 提供A股实时行情数据",
    version="1.0.0",
)

# 允许跨域（智谱清言服务器需要能访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 缓存层：3秒内重复请求返回缓存数据，避免频繁抓取被限流
# ============================================================
_cache = {}
CACHE_TTL = 3  # 秒

def get_cached(key, fetch_func):
    """简单缓存：3秒过期"""
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
    data = fetch_func()
    _cache[key] = (now, data)
    return data


# ============================================================
# API 接口
# ============================================================

@app.get("/")
def root():
    """健康检查"""
    return {"status": "ok", "service": "股票实时数据API", "time": datetime.now().isoformat()}


@app.get("/stock/realtime")
def get_realtime(symbol: str = Query(..., description="股票代码，如 sh600519, sz000001")):
    """
    获取A股实时行情
    - symbol 格式：sh + 6位代码（沪市）或 sz + 6位代码（深市）
    - 示例：sh600519（贵州茅台），sz000001（平安银行）
    """
    try:
        code = symbol[2:]  # 去掉 sh/sz 前缀
        market = symbol[:2].lower()

        # 从AKShare获取全A股实时行情
        def fetch():
            df = ak.stock_zh_a_spot_em()
            row = df[df['代码'] == code]
            if row.empty:
                return None
            return row.iloc[0].to_dict()

        data = get_cached(f"realtime_{symbol}", fetch)
        if data is None:
            return {"error": f"未找到股票代码: {symbol}", "hint": "请检查代码是否正确，如 sh600519"}

        return {
            "code": symbol,
            "name": data.get('名称', ''),
            "price": data.get('最新价', 0),
            "change_pct": data.get('涨跌幅', 0),
            "change_amount": data.get('涨跌额', 0),
            "volume": data.get('成交量', 0),
            "turnover": data.get('成交额', 0),
            "high": data.get('最高', 0),
            "low": data.get('最低', 0),
            "open": data.get('今开', 0),
            "prev_close": data.get('昨收', 0),
            "turnover_rate": data.get('换手率', 0),
            "pe_ratio": data.get('市盈率-动态', 0),
            "total_market_cap": data.get('总市值', 0),
            "circulating_market_cap": data.get('流通市值', 0),
            "amplitude": data.get('振幅', 0),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": f"获取数据失败: {str(e)}"}


@app.get("/stock/history")
def get_history(
    symbol: str = Query(..., description="股票代码，如 sh600519"),
    days: int = Query(30, description="获取最近N天的数据，默认30天"),
):
    """
    获取A股历史日K线数据
    """
    try:
        code = symbol[2:]
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"  # 前复权
        )
        df = df.tail(days)

        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row['日期']),
                "open": float(row['开盘']),
                "close": float(row['收盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "volume": float(row['成交量']),
                "turnover": float(row['成交额']),
                "change_pct": float(row['涨跌幅']) if '涨跌幅' in row else 0,
            })

        # 计算简单技术指标
        if len(records) >= 5:
            closes = [r["close"] for r in records]
            ma5 = sum(closes[-5:]) / 5
            records[-1]["ma5"] = round(ma5, 2)
        if len(records) >= 20:
            closes = [r["close"] for r in records]
            ma20 = sum(closes[-20:]) / 20
            records[-1]["ma20"] = round(ma20, 2)

        return {
            "code": symbol,
            "days": len(records),
            "history": records,
        }
    except Exception as e:
        return {"error": f"获取历史数据失败: {str(e)}"}


@app.get("/stock/search")
def search_stock(keyword: str = Query(..., description="股票名称或代码关键词，如 茅台, 600519")):
    """
    按名称或代码搜索股票
    """
    try:
        def fetch():
            df = ak.stock_zh_a_spot_em()
            # 按名称或代码匹配
            mask = df['名称'].str.contains(keyword) | df['代码'].str.contains(keyword)
            results = df[mask].head(10)
            return results.to_dict('records')

        data = get_cached(f"search_{keyword}", fetch)

        return {
            "keyword": keyword,
            "count": len(data),
            "results": [
                {
                    "code": f"sh{d['代码']}" if d['代码'].startswith('6') else f"sz{d['代码']}",
                    "name": d['名称'],
                    "price": d.get('最新价', 0),
                    "change_pct": d.get('涨跌幅', 0),
                }
                for d in data
            ]
        }
    except Exception as e:
        return {"error": f"搜索失败: {str(e)}"}


@app.get("/stock/market_overview")
def market_overview():
    """
    获取大盘概览：上证指数、深证成指、创业板指等
    """
    try:
        def fetch():
            # 获取主要指数
            df = ak.stock_zh_index_spot_em(symbol="指数成份")
            indices = {}
            for idx_name in ['上证指数', '深证成指', '创业板指', '科创50']:
                row = df[df['名称'] == idx_name]
                if not row.empty:
                    d = row.iloc[0]
                    indices[idx_name] = {
                        "code": d['代码'],
                        "price": d.get('最新价', 0),
                        "change_pct": d.get('涨跌幅', 0),
                        "change_amount": d.get('涨跌额', 0),
                        "volume": d.get('成交量', 0),
                        "turnover": d.get('成交额', 0),
                    }
            return indices

        data = get_cached("market_overview", fetch)
        return {"timestamp": datetime.now().isoformat(), "indices": data}
    except Exception as e:
        return {"error": f"获取大盘数据失败: {str(e)}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
