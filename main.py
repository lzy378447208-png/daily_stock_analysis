# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【智能分水岭版】15点前锁定上个交易日，15点后锁定当天收盘，完美兼容全时段动态抓取
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置
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
import akshare as ak
from datetime import datetime, timezone, timedelta

from data_provider.base import canonical_stock_code
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()


# ====================== 🎯 核心逻辑：智能时间判断分水岭（原生平替版） ======================
def get_last_trading_day_stocks() -> list:
    """
    自适应 A 股收盘逻辑（无需 pandas_market_calendars 依赖）：
    - 智能过滤周末，15点前自动回溯到上一个有效的交易日
    """
    try:
        now = datetime.now()
        
        # 1. 基础时间推算：15点前看前一天，15点后看今天
        if now.hour < 15:
            target_dt = now - timedelta(days=1)
        else:
            target_dt = now

        # 2. 智能跳过周末（如果落在了周六或周日，自动向前寻找周五）
        # weekday(): 0=周一, ..., 5=周六, 6=周日
        if target_dt.weekday() == 5:    # 如果是周六
            target_dt = target_dt - timedelta(days=1)  # 退回周五
        elif target_dt.weekday() == 6:  # 如果是周日
            target_dt = target_dt - timedelta(days=2)  # 退回周五

        raw_date = target_dt.strftime("%Y-%m-%d")
        
        if now.hour >= 15 and now.weekday() < 5:
            logger.info(f"⏰ 当前时间 {now.strftime('%H:%M')} 已收盘，目标锁定【今日收盘交易日】：{raw_date}")
        else:
            logger.info(f"⏰ 当前未收盘或处于非交易日，目标锁定【历史交易日】：{raw_date}")

        # 去除横杠适配 AkShare 格式 (如 20260515)
        target_date = raw_date.replace("-", "").strip()
        logger.info(f"📅 策略最终向 AkShare 发起请求的目标日期：【{target_date}】")

        logger.info(f"🚀 正在调取 AkShare 东方财富 {target_date} 真实涨停股数据...")
        try:
            df = ak.stock_zt_pool_em(date=target_date)
        except (ValueError, Exception) as e:
            # 温柔挂起，不让 Actions 崩溃变红
            logger.warning(f"⚠️ 调取接口时遭遇结构波动（东财服务器可能正在维护/清空 {target_date} 的数据）: {str(e)[:100]}")
            return []

        # 解析清洗代码
        stock_list = []
        if df is not None and not df.empty:
            code_col = "代码" if "代码" in df.columns else "code"
            if code_col in df.columns:
                for c in df[code_col].tolist():
                    c_str = str(c).strip()
                    if c_str and len(c_str) == 6 and c_str.isdigit():
                        # ✨ 核心过滤修改：只保留主板(60, 00)和创业板(30)
                        # 自动排除 688(科创板)、83/87/43(北交所)
                        if c_str.startswith(('60', '00', '30')):
                            stock_list.append(c_str)
                
                logger.info(f"✅ 成功提取并过滤 A 股主板/创业板【{target_date}】真实涨停个股共 {len(stock_list)} 只！")
                return stock_list

        logger.warning(f"⚠️ 接口响应成功，但【{target_date}】并未返回任何符合板块要求的涨停股票。")
        return []
            
    except Exception as e:
        logger.error(f"❌ 运行 get_last_trading_day_stocks 遭遇非预期异常: {str(e)}")
        return []
# =======================================================================================


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
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None
    return {
        str(key): "" if value is None else str(value)
        for key, value in values.items()
        if key is not None
    }

_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {
    key for key in _ACTIVE_ENV_FILE_VALUES
    if key not in _INITIAL_PROCESS_ENV
}

_env_bootstrapped = True

def _bootstrap_environment() -> None:
    global _env_bootstrapped
    if _env_bootstrapped:
        return
    from src.config import setup_env
    setup_env()
    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url
    _env_bootstrapped = True

def _setup_bootstrap_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)

def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning("文件日志初始化失败，已降级为控制台日志: %s", exc)
        return False

def _get_stock_analysis_pipeline():
    _bootstrap_environment()
    from src.core.pipeline import StockAnalysisPipeline as _Pipeline
    return _Pipeline

class _LazyPipelineDescriptor:
    _resolved = None
    def __set_name__(self, owner, name):
        self._name = name
    def __get__(self, obj, objtype=None):
        if self._resolved is None:
            self._resolved = _get_stock_analysis_pipeline()
        return self._resolved

class _ModuleExports:
    StockAnalysisPipeline = _LazyPipelineDescriptor()

_exports = _ModuleExports()

def __getattr__(name: str):
    if name == "StockAnalysisPipeline":
        return _exports.StockAnalysisPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _reload_env_file_values_preserving_overrides() -> None:
    global _RUNTIME_ENV_FILE_KEYS
    latest_values = _read_active_env_values()
    if latest_values is None:
        return
    managed_keys = {
        key for key in latest_values
        if key not in _INITIAL_PROCESS_ENV
    }
    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)
    for key in managed_keys:
        os.environ[key] = latest_values[key]
    _RUNTIME_ENV_FILE_KEYS = managed_keys

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='A股自选股智能分析系统')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--dry-run', action='store_true', help='仅获取数据不分析')
    parser.add_argument('--stocks', type=str, help='指定股票')
    parser.add_argument('--no-notify', action='store_true', help='不推送')
    parser.add_argument('--check-notify', action='store_true', help='检查推送')
    parser.add_argument('--single-notify', action='store_true', help='单股推送')
    parser.add_argument('--workers', type=int, default=None, help='并发数')
    parser.add_argument('--schedule', action='store_true', help='定时模式')
    parser.add_argument('--no-run-immediately', action='store_true', help='定时不立即执行')
    parser.add_argument('--market-review', action='store_true', help='仅大盘复盘')
    parser.add_argument('--no-market-review', action='store_true', help='跳过大盘复盘')
    parser.add_argument('--force-run', action='store_true', help='强制运行')
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

def _compute_trading_day_filter(
    config: Config, args: argparse.Namespace, stock_codes: List[str]
) -> Tuple[List[str], Optional[str], bool]:
    # 彻底移除不可靠的外部日历过滤拦截机制，改为由主流水线内部控制
    return (stock_codes, None, False)

def _run_market_review_with_shared_lock(
    config: Config, run_market_review_func: Callable[..., Optional[str]], **kwargs: Any
) -> Optional[str]:
    from src.core.market_review_lock import (
        release_market_review_lock, try_acquire_market_review_lock
    )
    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("大盘复盘正在执行中，跳过本次大盘复盘")
        return None
    try:
        return run_market_review_func(**kwargs)
    finally:
        release_market_review_lock(lock_token)

def run_full_analysis(
    config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None
):
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline
    try:
        if stock_codes is None:
            config.refresh_stock_list()

        # ====================== 🚀 对接智能时间清洗 ======================
        limit_up_stocks = get_last_trading_day_stocks()
        if limit_up_stocks:
            sanitized_stocks = []
            for c in limit_up_stocks:
                if not c or "ZTPOOL" in str(c).upper():
                    continue
                try:
                    std_code = canonical_stock_code(str(c).strip())
                    if std_code and "ZTPOOL" not in std_code.upper():
                        sanitized_stocks.append(std_code)
                except Exception as e:
                    logger.warning(f"⚠️ 代码 {c} 规范化清洗失败: {e}")
            
            if sanitized_stocks:
                effective_codes = sanitized_stocks
                config.stock_list = sanitized_stocks  
                logger.info(f"🎯 真实涨停股成功录入智能分析流水线（共 {len(effective_codes)} 只）")
            else:
                effective_codes = [c for c in config.stock_list if "ZTPOOL" not in str(c).upper()]
        else:
            effective_codes = [c for c in config.stock_list if "ZTPOOL" not in str(c).upper()]
            logger.info(f"📌 当前时段无可用动态涨停池，自动降级分析默认自选股（共 {len(effective_codes)} 只）")
        # ====================================================================

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

        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        pipeline = StockAnalysisPipeline(
            config=config, max_workers=args.workers, query_id=query_id,
            query_source="cli", save_context_snapshot=save_context_snapshot
        )

        results = pipeline.run(
            stock_codes=stock_codes, dry_run=args.dry_run,
            send_notification=not args.no_notify, merge_notification=merge_notification
        )

        if Bag := results:
            results = [r for r in results if r and hasattr(r, 'code') and "ZTPOOL" not in str(r.code).upper()]

        market_report = ""
        if (
            config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            review_result = _run_market_review_with_shared_lock(
                config, run_market_review, notifier=pipeline.notifier,
                analyzer=pipeline.analyzer, search_service=pipeline.search_service,
                send_notification=not args.no_notify, merge_notification=merge_notification,
                override_region=effective_region
            )
            if review_result:
                market_report = review_result

        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(
                    results, getattr(config, 'report_type', 'simple')
                )
                parts.append(f"# 🚀 真实涨停板智能推演分析报告\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report")

        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: getattr(x, 'sentiment_score', 60), reverse=True):
                emoji = getattr(r, 'get_emoji', lambda: "🔍")()
                score = getattr(r, 'sentiment_score', getattr(r, 'signal_score', 60))
                advice = getattr(r, 'operation_advice', '观望')
                logger.info(f"{emoji} {r.name}({r.code}): {advice} | 评分 {score}")

        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d')} A股量化复盘与热点题材推演"
                full_content = ""
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results, getattr(config, 'report_type', 'simple')
                    )
                    full_content += f"# 🚀 真实涨停个股技术与题材深度推演\n\n{dashboard_content}"
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url and not args.no_notify:
                    pipeline.notifier.send(f"量化复盘同步飞书文档已生成: {doc_url}", route_type="report")
        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")

def main() -> int:
    args = parse_arguments()
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception:
        logging.basicConfig(level=logging.INFO)
    try:
        config = get_config()
    except Exception:
        return 1
    
    _setup_runtime_logging(config.log_dir, debug=args.debug)

    if config.run_immediately:
        run_full_analysis(config, args, None)
    return 0

if __name__ == "__main__":
    sys.exit(main())
