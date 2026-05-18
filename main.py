# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【终极版】自动获取【上一个交易日】涨停，完美跳过周末/节假日
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
import requests
from datetime import datetime, timezone, timedelta

from data_provider.base import canonical_stock_code
from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()

# ====================== 【终极版】自动获取上一个交易日涨停 ======================
def get_last_trading_day_stocks() -> list:
    """
    🔥 终极版：永远取【上一个已收盘交易日】
    自动跳过：周六、周日、A股节假日
    15:00前 → 上一交易日
    15:00后 → 今日（已收盘）
    """
    try:
        now = datetime.now()
        today = now.date()
        wd = today.weekday()

        # 1. 先判断今天是否是交易日（周一~周五）
        if wd in (5, 6):
            # 今天是周六/周日 → 强制取上周五
            days_ago = wd + 1
            target_date = (now - timedelta(days=days_ago)).strftime("%Y%m%d")
            logger.info(f"⏳ 今日周末，自动取上周五行情：{target_date}")
        else:
            # 今天是周一~周五
            if now.hour >= 15:
                # 已收盘 → 取今天
                target_date = now.strftime("%Y%m%d")
                logger.info(f"✅ 今日已收盘 → 获取【今日】涨停：{target_date}")
            else:
                # 未收盘 → 取前一个交易日
                if wd == 0:
                    # 周一 → 取上周五
                    target_date = (now - timedelta(days=3)).strftime("%Y%m%d")
                    logger.info(f"⏳ 周一未收盘 → 自动取上周五：{target_date}")
                else:
                    # 周二~周五 → 取昨天
                    target_date = (now - timedelta(days=1)).strftime("%Y%m%d")
                    logger.info(f"⏳ 今日未收盘 → 获取【昨日】涨停：{target_date}")

        # 请求接口
        url = f"http://81.70.189.13:8080/api/stock/limitup?date={target_date}"
        res = requests.get(url, timeout=12)
        data = res.json()

        stock_list = []
        for item in data.get("data", []):
            code = item.get("code", "")
            if code and len(code) == 6:
                stock_list.append(code)

        if stock_list:
            logger.info(f"✅ 获取到【{target_date}】涨停股票 {len(stock_list)} 只")
            return stock_list
        else:
            logger.warning(f"⚠️ {target_date} 无涨停股票，使用自选股")
            return []
    except Exception as e:
        logger.error(f"❌ 获取涨停失败: {str(e)}")
        return []
# ============================================================================

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
    parser = argparse.ArgumentParser(
        description='A股自选股智能分析系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python main.py              # 正常运行
  python main.py --debug      # 调试模式
  python main.py --dry-run    # 仅获取数据不分析
'''
    )
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
    force_run = getattr(args, 'force_run', False)
    if force_run or not getattr(config, 'trading_day_check_enabled', True):
        return (stock_codes, None, False)
    from src.core.trading_calendar import (
        get_market_for_stock, get_open_markets_today, compute_effective_region
    )
    open_markets = get_open_markets_today()
    filtered_codes = []
    for code in stock_codes:
        mkt = get_market_for_stock(code)
        if mkt in open_markets or mkt is None:
            filtered_codes.append(code)
    if config.market_review_enabled and not getattr(args, 'no_market_review', False):
        effective_region = compute_effective_region(
            getattr(config, 'market_review_region', 'cn') or 'cn', open_markets
        )
    else:
        effective_region = None
    should_skip_all = (not filtered_codes) and (effective_region or '') == ''
    return (filtered_codes, effective_region, should_skip_all)

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

        # ====================== 核心调用 ======================
        limit_up_stocks = get_last_trading_day_stocks()
        if limit_up_stocks:
            effective_codes = limit_up_stocks
            logger.info(f"🚀 分析目标：上一交易日涨停 {len(effective_codes)} 只")
        else:
            effective_codes = config.stock_list
            logger.info(f"📌 使用自选股：{len(effective_codes)} 只")
        # ======================================================

        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config, args, effective_codes
        )
        if should_skip:
            logger.info("今日非交易日，跳过执行")
            return
        if set(filtered_codes) != set(effective_codes):
            skipped = set(effective_codes) - set(filtered_codes)
            logger.info("今日休市股票已跳过: %s", skipped)
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

        analysis_delay = getattr(config, 'analysis_delay', 0)
        if (
            analysis_delay > 0
            and config.market_review_enabled
            and not args.no_market_review
            and effective_region != ''
        ):
            logger.info(f"等待 {analysis_delay} 秒后执行大盘复盘")
            time.sleep(analysis_delay)

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
                parts.append(f"# 🚀 上一交易日涨停分析\n\n{dashboard_content}")
            if parts:
                combined_content = "\n\n---\n\n".join(parts)
                if pipeline.notifier.is_available():
                    if pipeline.notifier.send(combined_content, email_send_to_all=True, route_type="report"):
                        logger.info("已合并推送")
                    else:
                        logger.warning("合并推送失败")

        if results:
            logger.info("\n===== 分析结果摘要 =====")
            for r in sorted(results, key=lambda x: x.sentiment_score, reverse=True):
                emoji = r.get_emoji()
                logger.info(f"{emoji} {r.name}({r.code}): {r.operation_advice} | 评分 {r.sentiment_score}")

        logger.info("\n任务执行完成")

        try:
            from src.feishu_doc import FeishuDocManager
            feishu_doc = FeishuDocManager()
            if feishu_doc.is_configured() and (results or market_report):
                tz_cn = timezone(timedelta(hours=8))
                now = datetime.now(tz_cn)
                doc_title = f"{now.strftime('%Y-%m-%d')} 上一交易日涨停分析"
                full_content = ""
                if market_report:
                    full_content += f"# 📈 大盘复盘\n\n{market_report}\n\n---\n\n"
                if results:
                    dashboard_content = pipeline.notifier.generate_aggregate_report(
                        results, getattr(config, 'report_type', 'simple')
                    )
                    full_content += f"# 🚀 涨停个股分析\n\n{dashboard_content}"
                doc_url = feishu_doc.create_daily_doc(doc_title, full_content)
                if doc_url:
                    logger.info(f"飞书文档创建成功: {doc_url}")
                    if not args.no_notify:
                        pipeline.notifier.send(f"涨停分析文档: {doc_url}", route_type="report")
        except Exception as e:
            logger.error(f"飞书文档生成失败: {e}")

    except Exception as e:
        logger.exception(f"分析流程执行失败: {e}")

def start_api_server(host: str, port: int, config: Config) -> None:
    import threading
    import uvicorn
    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run("api.app:app", host=host, port=port, log_level=level_name, log_config=None)
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")

def _is_truthy_env(var_name: str, default: str = "true") -> bool:
    value = os.getenv(var_name, default).strip().lower()
    return value not in {"0", "false", "no", "off"}

def start_bot_stream_clients(config: Config) -> None:
    if config.dingtalk_stream_enabled:
        try:
            from bot.platforms import start_dingtalk_stream_background, DINGTALK_STREAM_AVAILABLE
            if DINGTALK_STREAM_AVAILABLE:
                if start_dingtalk_stream_background():
                    logger.info("[Main] Dingtalk Stream client started")
                else:
                    logger.warning("[Main] Dingtalk Stream client failed")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Dingtalk Stream: {exc}")
    if getattr(config, 'feishu_stream_enabled', False):
        try:
            from bot.platforms import start_feishu_stream_background, FEISHU_SDK_AVAILABLE
            if FEISHU_SDK_AVAILABLE:
                if start_feishu_stream_background():
                    logger.info("[Main] Feishu Stream client started")
                else:
                    logger.warning("[Main] Feishu Stream client failed")
        except Exception as exc:
            logger.error(f"[Main] Failed to start Feishu Stream: {exc}")

def _resolve_scheduled_stock_codes(stock_codes: Optional[List[str]]) -> Optional[List[str]]:
    if stock_codes is not None:
        logger.warning("定时模式忽略--stocks，每次运行读取最新STOCK_LIST")
    return None

def _reload_runtime_config() -> Config:
    _reload_env_file_values_preserving_overrides()
    Config.reset_instance()
    return get_config()

def _build_schedule_time_provider(default_schedule_time: str):
    from src.core.config_manager import ConfigManager
    _SYSTEM_DEFAULT_SCHEDULE_TIME = "16:30"
    manager = ConfigManager()
    def _provider() -> str:
        if "SCHEDULE_TIME" in _INITIAL_PROCESS_ENV:
            return os.getenv("SCHEDULE_TIME", default_schedule_time)
        config_map = manager.read_config_map()
        schedule_time = (config_map.get("SCHEDULE_TIME", "") or "").strip()
        if schedule_time:
            return schedule_time
        return _SYSTEM_DEFAULT_SCHEDULE_TIME
    return _provider

def main() -> int:
    args = parse_arguments()
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
        logger.warning("Bootstrap日志初始化失败: %s", exc)
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("加载配置失败: %s", exc)
        return 1
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("切换日志目录失败: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("【已启用】自动获取上一交易日涨停（自动跳过周末/节假日）")
    logger.info("=" * 60)

    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    if getattr(args, "check_notify", False):
        from src.services.notification_diagnostics import format_notification_diagnostics, run_notification_diagnostics
        result = run_notification_diagnostics(config)
        print(format_notification_diagnostics(result))
        return 0 if result.ok else 1

    stock_codes = None
    if args.stocks:
        stock_codes = [canonical_stock_code(c) for c in args.stocks.split(',') if (c or "").strip()]
        logger.info(f"使用命令行股票: {stock_codes}")

    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"
    if start_serve:
        if args.host == '0.0.0.0' and os.getenv('WEBUI_HOST'):
            args.host = os.getenv('WEBUI_HOST')
        if args.port == 8000 and os.getenv('WEBUI_PORT'):
            args.port = int(os.getenv('WEBUI_PORT'))

    bot_clients_started = False
    if start_serve:
        if not prepare_webui_frontend_assets():
            logger.warning("前端静态资源未就绪，Web页面可能不可用")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
            bot_clients_started = True
        except Exception as e:
            logger.error(f"启动FastAPI失败: {e}")
    if bot_clients_started:
        start_bot_stream_clients(config)

    if args.serve_only:
        logger.info("模式: 仅Web服务")
        logger.info(f"运行中: http://{args.host}:{args.port}")
        logger.info("按Ctrl+C退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\n用户中断，退出")
        return 0

    try:
        if getattr(args, 'backtest', False):
            logger.info("模式: 回测")
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            stats = service.run_backtest(
                code=args.backtest_code, force=args.backtest_force, eval_window_days=args.backtest_days
            )
            logger.info(f"回测完成: processed={stats.get('processed')}")
            return 0

        if args.market_review:
            from src.core.market_review import run_market_review
            from src.core.market_review_runtime import build_market_review_runtime
            effective_region = None
            if not args.force_run and config.trading_day_check_enabled:
                from src.core.trading_calendar import get_open_markets_today, compute_effective_region
                open_markets = get_open_markets_today()
                effective_region = compute_effective_region(config.market_review_region or 'cn', open_markets)
                if effective_region == '':
                    logger.info("今日非交易日，跳过大盘复盘")
                    return 0
            logger.info("模式: 仅大盘复盘")
            notifier, analyzer, search_service = build_market_review_runtime(config)
            _run_market_review_with_shared_lock(
                config, run_market_review, notifier=notifier, analyzer=analyzer, search_service=search_service,
                send_notification=not args.no_notify, override_region=effective_region
            )
            return 0

        if args.schedule or config.schedule_enabled:
            logger.info("模式: 定时任务")
            logger.info(f"每日执行时间: {config.schedule_time}")
            should_run_immediately = config.schedule_run_immediately
            if args.no_run_immediately:
                should_run_immediately = False
            logger.info(f"启动时立即执行: {should_run_immediately}")

            from src.scheduler import run_with_schedule
            scheduled_stock_codes = _resolve_scheduled_stock_codes(stock_codes)
            schedule_time_provider = _build_schedule_time_provider(config.schedule_time)

            def scheduled_task():
                runtime_config = _reload_runtime_config()
                run_full_analysis(runtime_config, args, scheduled_stock_codes)

            background_tasks = []
            if config.agent_event_monitor_enabled:
                from src.services.alert_worker import AlertWorker
                interval_minutes = max(1, config.agent_event_monitor_interval_minutes)
                alert_worker = AlertWorker(config_provider=_reload_runtime_config)

                def event_monitor_task():
                    stats = alert_worker.run_once()
                    triggered_count = stats.get("triggered", 0)
                    if triggered_count:
                        logger.info("[EventMonitor] 本轮触发 %d 条提醒", triggered_count)

                background_tasks.append({
                    "task": event_monitor_task,
                    "interval_seconds": interval_minutes * 60,
                    "run_immediately": True,
                    "name": "agent_event_monitor",
                })

            run_with_schedule(
                task=scheduled_task, schedule_time=config.schedule_time,
                run_immediately=should_run_immediately, background_tasks=background_tasks,
                schedule_time_provider=schedule_time_provider
            )
            return 0

        if config.run_immediately:
            run_full_analysis(config, args, stock_codes)
        else:
            logger.info("配置为不立即运行分析")

        logger.info("\n程序执行完成")
        keep_running = start_serve and not (args.schedule or config.schedule_enabled)
        if keep_running:
            logger.info("API服务运行中，按Ctrl+C退出")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return 0

    except KeyboardInterrupt:
        logger.info("\n用户中断，退出")
        return 130
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
