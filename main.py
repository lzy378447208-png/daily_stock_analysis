# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【稳定终版】自动涨停抓取 + 北京时间校准 + 邮件强制修复
"""
from __future__ import annotations

# 🛡️ 顶级安全防护：设置硬超时，防止网络死锁
import socket
socket.setdefaulttimeout(8.0)

try:
    import urllib3
    urllib3.util.timeout.Timeout.DEFAULT_TIMEOUT = 8.0
    
    import requests
    from requests.adapters import HTTPAdapter
    class TopPriorityTimeoutAdapter(HTTPAdapter):
        def __init__(self, *args, **kwargs):
            self.timeout = 8.0
            if "timeout" in kwargs:
                self.timeout = kwargs["timeout"]
                del kwargs["timeout"]
            super().__init__(*args, **kwargs)
        def send(self, request, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = self.timeout
            return super().send(request, **kwargs)
            
    _old_session_init = requests.Session.__init__
    def _patched_session_init(self, *args, **kwargs):
        _old_session_init(self, *args, **kwargs)
        adapter = TopPriorityTimeoutAdapter()
        self.mount("http://", adapter)
        self.mount("https://", adapter)
    requests.Session.__init__ = _patched_session_init
except Exception:
    pass

# ==================== 标准库与核心环境加载 ====================
import os
import sys
import argparse
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from dotenv import dotenv_values

# ==============================================
# ✅ ✅ ✅ 【邮件终极修复】自动兼容单数/复数变量
# 你 GitHub 里的 EMAIL_RECEIVER 自动生效
# ==============================================
if os.getenv("EMAIL_RECEIVER") and not os.getenv("EMAIL_RECEIVERS"):
    os.environ["EMAIL_RECEIVERS"] = os.getenv("EMAIL_RECEIVER")

from src.config import setup_env
_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import akshare as ak
from data_provider.base import canonical_stock_code
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)
_HAS_REAL_DYNAMIC_DATA = False
_TARGET_DATE_STR = ""

def get_beijing_time() -> datetime:
    """获取标准北京时间"""
    tz_beijing = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_beijing)

# ====================== 🎯 核心：自动获取涨停股 ======================
def get_last_trading_day_stocks() -> list:
    global _HAS_REAL_DYNAMIC_DATA, _TARGET_DATE_STR
    try:
        now = get_beijing_time()
        
        if now.hour < 15:
            target_dt = now - timedelta(days=1)
        else:
            target_dt = now

        if target_dt.weekday() == 5:    
            target_dt -= timedelta(days=1)
        elif target_dt.weekday() == 6:  
            target_dt -= timedelta(days=2)

        raw_date = target_dt.strftime("%Y-%m-%d")
        _TARGET_DATE_STR = raw_date
        target_date = raw_date.replace("-", "")
        
        logger.info(f"⏰ 北京时间：{now.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"📅 抓取涨停数据：{target_date}")

        try:
            df = ak.stock_zt_pool_em(date=target_date)
        except Exception as e:
            logger.warning(f"⚠️ 数据接口异常：{str(e)[:100]}")
            return []

        if df is not None and not df.empty:
            code_col = "代码" if "代码" in df.columns else "code"
            df[code_col] = df[code_col].astype(str).str.strip()
            df = df[df[code_col].str.startswith(('60', '00', '30'))]
            
            raw_list = df[code_col].tolist()
            final_30 = raw_list[:30]
            _HAS_REAL_DYNAMIC_DATA = True
            return final_30

        logger.warning("⚠️ 未获取到涨停数据")
        return []
    except Exception as e:
        logger.error(f"❌ 涨停数据获取失败：{e}")
        return []

# ====================== 以下是你原来能正常发邮件的完整代码 ======================
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
        logger.warning("读取配置文件失败：%s", exc)
        return None
    return {str(key): "" if value is None else str(value) for key, value in values.items() if key is not None}

_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {key for key in _ACTIVE_ENV_FILE_VALUES if key not in _INITIAL_PROCESS_ENV}
_env_bootstrapped = True

def _bootstrap_environment() -> None:
    global _env_bootstrapped
    if _env_bootstrapped:
        return
    from src.config import setup_env
    setup_env()
    _env_bootstrapped = True

def _setup_bootstrap_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(handler)

def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning("文件日志初始化失败：%s", exc)
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
    managed_keys = {key for key in latest_values if key not in _INITIAL_PROCESS_ENV}
    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)
    for key in managed_keys:
        os.environ[key] = latest_values[key]
    _RUNTIME_ENV_FILE_KEYS = managed_keys

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
    try:
        from src.core.trading_calendar import get_market_for_stock, get_open_markets_today, compute_effective_region
        open_markets = get_open_markets_today()
        filtered_codes = [code for code in stock_codes if get_market_for_stock(code) in open_markets or get_market_for_stock(code) is None]
        effective_region = compute_effective_region(getattr(config, 'market_review_region', 'cn') or 'cn', open_markets) if config.market_review_enabled and not getattr(args, 'no_market_review', False) else None
        should_skip_all = (not filtered_codes) and (effective_region or '') == ''
        return (filtered_codes, effective_region, should_skip_all)
    except Exception as e:
        logger.warning("⚠️ 交易日历异常：%s", e)
        return (stock_codes, "cn", False)

def _run_market_review_with_shared_lock(config: Config, run_market_review_func: Callable[..., Optional[str]], **kwargs: Any) -> Optional[str]:
    from src.core.market_review_lock import release_market_review_lock, try_acquire_market_review_lock
    lock_token = try_acquire_market_review_lock(config)
    if lock_token is None:
        logger.warning("大盘复盘正在执行中，跳过本次")
        return None
    try:
        return run_market_review_func(**kwargs)
    finally:
        release_market_review_lock(lock_token)

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    try:
        if stock_codes is None:
            config.refresh_stock_list()

        # 自动加载涨停股
        limit_up_stocks = get_last_trading_day_stocks()
        if limit_up_stocks:
            sanitized = []
            for c in limit_up_stocks:
                try:
                    std = canonical_stock_code(str(c).strip())
                    if std and std.startswith(('60', '00', '30')):
                        sanitized.append(std)
                except Exception:
                    continue
            effective_codes = sanitized[:30]
            config.stock_list = effective_codes
            logger.info(f"🎯 已加载涨停股：{len(effective_codes)} 只")
        else:
            effective_codes = [c for c in config.stock_list if str(c).startswith(('60', '00', '30'))][:30]

        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(config, args, effective_codes)
        if should_skip:
            logger.info("今日非交易日，跳过")
            return
        stock_codes = filtered_codes

        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        merge_notification = getattr(config, 'merge_email_notification', False) and config.market_review_enabled and not getattr(args, 'no_market_review', False) and not config.single_stock_notify
        save_context_snapshot = None
        if getattr(args, 'no_context_snapshot', False):
            save_context_snapshot = False
        query_id = uuid.uuid4().hex
        workers = args.workers if args.workers is not None else 2
        pipeline = StockAnalysisPipeline(config=config, max_workers=workers, query_id=query_id, query_source="cli", save_context_snapshot=save_context_snapshot)
        results = pipeline.run(stock_codes=stock_codes, dry_run=args.dry_run, send_notification=not args.no_notify, merge_notification=merge_notification)

        market_report = ""
        if config.market_review_enabled and not args.no_market_review and effective_region != '':
            review_result = _run_market_review_with_shared_lock(config, run_market_review, notifier=pipeline.notifier, analyzer=pipeline.analyzer, search_service=pipeline.search_service, send_notification=not args.no_notify, merge_notification=merge_notification, override_region=effective_region)
            if review_result:
                market_report = review_result

        if merge_notification and (results or market_report) and not args.no_notify:
            parts = []
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))
                parts.append(f"# 🚀 个股分析报告\n\n{dashboard_content}")
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
                doc_title = f"{now.strftime('%Y-%m-%d')} A股量化复盘"
                full_content = ""
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))
                    full_content += f"# 🚀 个股深度分析\n\n{dashboard_content}"
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url and not args.no_notify:
                    pipeline.notifier.send(f"复盘文档已生成：{doc_url}", route_type="report")
        except Exception as e:
            logger.error(f"飞书文档生成失败：{e}")

    except Exception as e:
        logger.exception(f"执行失败：{e}")

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
