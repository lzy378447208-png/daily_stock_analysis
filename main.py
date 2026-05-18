# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【终极优化版】集成：1. 题材涨停天梯图 2. 分时封板质量量化
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from collections import defaultdict

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
import uuid
import requests
from datetime import datetime, timezone, timedelta

from data_provider.base import canonical_stock_code
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)

# 全局缓存：用于在个股分析时读取丰富的短线指标
GLOBAL_LIMIT_UP_DETAIL_MAP: Dict[str, Dict[str, Any]] = {}

def get_last_trading_day_stocks() -> list:
    """
    获取上一个已收盘交易日的涨停板股票详情
    同时缓存：连板天数、题材热点、首次封板时间、炸板次数等分时与短线指标
    """
    global GLOBAL_LIMIT_UP_DETAIL_MAP
    try:
        now = datetime.now()
        today = now.date()
        wd = today.weekday()

        if wd in (5, 6):
            days_ago = wd + 1
            target_date = (now - timedelta(days=days_ago)).strftime("%Y%m%d")
            logger.info(f"⏳ 今日周末，自动取上周五行情：{target_date}")
        else:
            if now.hour >= 15:
                target_date = now.strftime("%Y%m%d")
                logger.info(f"✅ 今日已收盘 → 获取【今日】涨停：{target_date}")
            else:
                if wd == 0:
                    target_date = (now - timedelta(days=3)).strftime("%Y%m%d")
                    logger.info(f"⏳ 周一未收盘 → 自动取上周五：{target_date}")
                else:
                    target_date = (now - timedelta(days=1)).strftime("%Y%m%d")
                    logger.info(f"⏳ 今日未收盘 → 获取【昨日】涨停：{target_date}")

        url = f"http://81.70.189.13:8080/api/stock/limitup?date={target_date}"
        res = requests.get(url, timeout=12)
        data = res.json()

        stock_list = []
        GLOBAL_LIMIT_UP_DETAIL_MAP.clear()

        for item in data.get("data", []):
            code = item.get("code", "")
            if code and len(code) == 6:
                stock_list.append(code)
                # 提取并清洗第2、3点所需的短线核心量化指标
                GLOBAL_LIMIT_UP_DETAIL_MAP[code] = {
                    "name": item.get("name", "未知"),
                    "reason": item.get("reason", "未明题材"),                   # 涨停题材
                    "height": int(item.get("height", 1)),                        # 连板高度 (例: 3代表3连板)
                    "status_text": item.get("status_text", "首板"),               # 几天几板描述
                    "first_time": item.get("first_time", "09:30:00"),            # 首次封板时间
                    "open_count": int(item.get("open_count", 0)),                # 炸板次数
                    "font_money": item.get("font_money", "未知")                 # 封单资金
                }

        if stock_list:
            logger.info(f"✅ 成功获取【{target_date}】涨停大池共 {len(stock_list)} 只股票并缓存短线量化指标")
            return stock_list
        else:
            logger.warning(f"⚠️ {target_date} 接口未返回涨停股票数据")
            return []
    except Exception as e:
        logger.error(f"❌ 获取涨停失败: {str(e)}")
        return []

def generate_market_ladder_summary() -> str:
    """
    🔥 [第2点功能实现]：遍历昨日涨停池，全自动生成【全市场题材天梯图摘要】
    """
    if not GLOBAL_LIMIT_UP_DETAIL_MAP:
        return ""
    
    # 1. 按连板高度对股票归类
    ladder_map = defaultdict(list)
    # 2. 按题材对股票归类（热词热点统计）
    concept_map = defaultdict(int)

    for code, info in GLOBAL_LIMIT_UP_DETAIL_MAP.items():
        ladder_map[info["height"]].append(f"{info['name']}({info['status_text']})")
        # 简单按“+”或空格拆分题材热词进行大局观统计
        concepts = info["reason"].replace(" ", "+").split("+")
        for c in concepts:
            if c.strip():
                concept_map[c.strip()] += 1

    # 3. 组装天梯图文本
    lines = [
        "## 🪜 全市场强短线题材天梯图",
        "市场总梯队纵览，一眼捕捉资金聚焦的主线战场：\n"
    ]
    
    # 从最高板往下排列
    for height in sorted(ladder_map.keys(), reverse=True):
        stocks_str = "、".join(ladder_map[height])
        if height >= 3:
            lines.append(f"🏆 **【高标梯队 ({height}连板及以上)】**：{stocks_str}")
        elif height == 2:
            lines.append(f"🥈 **【中军接力 (2连板身位)】**：{stocks_str}")
        else:
            # 首板股票太多时，只展示前5个示范，避免报告过长
            lines.append(f"🌱 **【蓄势首板 (1板动能池)】**：{', '.join(ladder_map[height][:8])} 等共 {len(ladder_map[height])} 只")

    # 4. 组装最强最热题材前三名
    top_concepts = sorted(concept_map.items(), key=lambda x: x[1], reverse=True)[:3]
    if top_concepts:
        concept_str = " | ".join([f"🔥 {k} ({v}家涨停)" for k, v in top_concepts])
        lines.append(f"\n📢 **全市场最热主线核心爆发点**：{concept_str}\n")
    
    return "\n".join(lines)

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline
    try:
        if stock_codes is None:
            config.refresh_stock_list()

        raw_stock_env = os.getenv("STOCK_LIST", "").strip().upper()
        
        if not raw_stock_env or raw_stock_env == "ZT_POOL":
            logger.info("⚡ 检测到配置要求分析上个交易日涨停池，开始调用实时数据源...")
            limit_up_stocks = get_last_trading_day_stocks()
            if limit_up_stocks:
                effective_codes = limit_up_stocks
                logger.info(f"🚀 成功切入全自动模式：上一交易日涨停共 {len(effective_codes)} 只股票进入分析流")
            else:
                effective_codes = []
                logger.warning("⚠️ 未能抓取到有效的涨停数据，本次任务不产生无意义分析报告。")
        else:
            effective_codes = config.stock_list
            logger.info(f"📌 正常解析自选股股票池：{len(effective_codes)} 只")

        if not effective_codes:
            logger.info("当前无有效股票代码需要处理，执行退出。")
            return

        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info("今日非交易日，跳过执行")
            return
        stock_codes = filtered_codes

        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        merge_notification = (
            getattr(config, 'merge_email_notification', False)
            and config.market_review_enabled
            and not getattr(args, 'no_market_review', False)
            and not config.single_stock_notify
        )

        save_context_snapshot = getattr(args, 'no_context_snapshot', False) is False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config, max_workers=args.workers, query_id=query_id,
            query_source="cli", save_context_snapshot=save_context_snapshot
        )

        results = pipeline.run(
            stock_codes=stock_codes, dry_run=args.dry_run,
            send_notification=not args.no_notify, merge_notification=merge_notification
        )

        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != '':
            review_result = _run_market_review_with_shared_lock(
                config, run_market_review, notifier=pipeline.notifier,
                analyzer=pipeline.analyzer, search_service=pipeline.search_service,
                send_notification=not args.no_notify, merge_notification=merge_notification,
                override_region=effective_region
            )
            if review_result:
                market_report = review_result

        # ====================== 🚀 【天梯图合并推送优化】 ======================
        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            
            # 【核心增补】在个股详情前，强力注入全市场题材天梯图
            ladder_summary = generate_market_ladder_summary()
            if ladder_summary:
                parts.append(ladder_summary)

            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results, getattr(config, 'report_type', 'simple')
                )
                parts.append(f"# 🚀 上一交易日强标个股深度剖析\n\n{dashboard_content}")
            
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("已合并推送（包含题材天梯图）")
                    else:
                        logger.warning("合并推送失败")
        # ======================================================================

        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(f"{emoji} {r.name}({r.code}): {r.operation_advice} | 评分 {r.sentiment_score}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")

# 以下保持原有系统骨架不变...
def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"

def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}
    try:
        values = dotenv_values(env_path)
    except Exception as exc:
        logger.warning("读取配置文件 %s 失败: %s", env_path, exc)
        return None
    return {str(key): "" if value is None else str(value) for key, value in values.items() if key is not None}

_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {key for key in _ACTIVE_ENV_FILE_VALUES if key not in _INITIAL_PROCESS_ENV}
_env_bootstrapped = True

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='A股自选股智能分析系统')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--stocks', type=str)
    parser.add_argument('--no-notify', action='store_true')
    parser.add_argument('--check-notify', action='store_true')
    parser.add_argument('--single-notify', action='store_true')
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--schedule', action='store_true')
    parser.add_argument('--no-run-immediately', action='store_true')
    parser.add_argument('--market-review', action='store_true')
    parser.add_argument('--no-market-review', action='store_true')
    parser.add_argument('--force-run', action='store_true')
    parser.add_argument('--webui', action='store_true')
    parser.add_argument('--webui-only', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--serve-only', action='store_true')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', type=str, default='0.0.0.0')
    parser.add_argument('--no-context-snapshot', action='store_true')
    parser.add_argument('--backtest', action='store_true')
    parser.add_argument('--backtest-code', type=str, default=None)
    parser.add_argument('--backtest-days', type=int, default=None)
    parser.add_argument('--backtest-force', action='store_true')
    return parser.parse_args()

def _compute_trading_day_filter(config: Config, args: argparse.Namespace, stock_codes: List[str]) -> Tuple[List[str], Optional[str], bool]:
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)
    from src.core.trading_calendar import get_market_for_stock, get_open_markets_today, compute_effective_region
    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)
    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(getattr(config, 'market_review_region', 'cn') or 'cn', open_markets)
    else:
        effective_region = None
    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)

def _run_market_review_with_shared_lock(config: Config, run_market_review_func: Callable[..., Optional[str]], **kwargs: Any) -> Optional[str]:
    from src.core.market_review_lock import release_market_review_lock, try_acquire_market_review_lock
    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        return None
    try:
        return run_market_review_func(**kwargs)
    finally:
        release_market_review_lock(lock_token)

def main() -> int:
    args = parse_arguments()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
    try:
        config = get_config()
    except Exception as exc:
        return 1
    
    logger.info("=" * 60)
    logger.info("A股智能分析系统启动：天梯图与分时质量量化组件加载完成")
    logger.info("=" * 60)

    if config.run_immediately:
        run_full_analysis(config, args, None)
    return 0

if __name__ == "__main__":
    sys.exit(main())
