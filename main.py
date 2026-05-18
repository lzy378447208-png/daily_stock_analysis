# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【强固时区 + 强制发信补丁版】完美解决 GitHub Actions 时区造成的漏发问题
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

# 全局变量，记录运行状态
_HAS_REAL_DYNAMIC_DATA = False
_TARGET_DATE_STR = ""

def get_beijing_time() -> datetime:
    """获取标准的北京时间 (UTC+8)"""
    tz_beijing = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_beijing)

def get_last_trading_day_stocks() -> list:
    global _HAS_REAL_DYNAMIC_DATA, _TARGET_DATE_STR
    try:
        # 🚨 修正：强制使用北京时间判断，不再用服务器本地时间
        now = get_beijing_time()
        
        if now.hour < 15:
            target_dt = now - timedelta(days=1)
        else:
            target_dt = now

        if target_dt.weekday() == 5:    
            target_dt = target_dt - timedelta(days=1)
        elif target_dt.weekday() == 6:  
            target_dt = target_dt - timedelta(days=2)

        raw_date = target_dt.strftime("%Y-%m-%d")
        _TARGET_DATE_STR = raw_date
        target_date = raw_date.replace("-", "").strip()
        
        logger.info(f"⏰ 当前北京时间：{now.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"📅 策略最终向 AkShare 发起请求的目标日期：【{target_date}】")

        try:
            df = ak.stock_zt_pool_em(date=target_date)
        except (ValueError, Exception) as e:
            logger.warning(f"⚠️ 调取动态涨停池网络遭遇阻碍: {str(e)[:100]}")
            return []

        if df is not None and not df.empty:
            code_col = "代码" if "代码" in df.columns else "code"
            lbc_col = "连续涨停天数" if "连续涨停天数" in df.columns else "fbs"  
            fbsj_col = "最后封板时间" if "最后封板时间" in df.columns else "fbt" 
            
            if code_col in df.columns:
                df[code_col] = df[code_col].astype(str).str.strip()
                df = df[df[code_col].str.startswith(('60', '00', '30'))].copy()
                
                sort_cols = []
                sort_ascending = []
                if lbc_col in df.columns:
                    df[lbc_col] = sys.float_info.min
                    try:
                        df[lbc_col] = df[lbc_col].astype(str).str.extract(r'(\d+)').astype(float)
                    except Exception:
                        pass
                    sort_cols.append(lbc_col)
                    sort_ascending.append(False) 
                    
                if fbsj_col in df.columns:
                    sort_cols.append(fbsj_col)
                    sort_ascending.append(True)  
                    
                if sort_cols:
                    df = df.sort_values(by=sort_cols, ascending=sort_ascending)
                
                raw_list = df[code_col].tolist()
                final_30 = raw_list[:30]
                if final_30:
                    _HAS_REAL_DYNAMIC_DATA = True
                    logger.info(f"✅ 成功获取动态涨停数据，截取核心【龙头30强】标的。")
                    return final_30

        return []
    except Exception as e:
        logger.error(f"❌ 运行 get_last_trading_day_stocks 遭遇异常: {e}")
        return []

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
        return (filtered_codes, effective_region, (not filtered_codes) and (effective_region or '') == '')
    except Exception as e:
        logger.warning(f"⚠️ 交易日历超时，自动放行: {e}")
        return (stock_codes, "cn", False)

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    global _HAS_REAL_DYNAMIC_DATA, _TARGET_DATE_STR
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline
    
    pipeline_instance = None
    market_report = ""
    results = []
    
    try:
        if stock_codes is None:
            config.refresh_stock_list()

        limit_up_stocks = get_last_trading_day_stocks()
        if limit_up_stocks:
            sanitized_stocks = []
            for c in limit_up_stocks:
                try:
                    std_code = canonical_stock_code(str(c).strip())
                    if std_code and std_code.startswith(('60', '00', '30')):
                        sanitized_stocks.append(std_code)
                except Exception:
                    pass
            effective_codes = sanitized_stocks[:30] if sanitized_stocks else [c for c in config.stock_list if str(c).startswith(('60', '00', '30'))][:30]
        else:
            effective_codes = [c for c in config.stock_list if str(c).startswith(('60', '00', '30'))][:30]
        
        config.stock_list = effective_codes  

        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(config, args, effective_codes)
        
        # 🚀 提速：并发数提升至 5
        workers = args.workers if args.workers is not None else 5
        pipeline_instance = StockAnalysisPipeline(
            config=config, max_workers=workers, query_id=uuid.uuid4().hex,
            query_source="cli", save_context_snapshot=False
        )

        if should_skip:
            logger.info("今日非交易日，触发安全跳过，直接进入邮件回执阶段")
        else:
            stock_codes = filtered_codes
            merge_notification = getattr(config, 'merge_email_notification', False) and config.market_review_enabled and not getattr(args, 'no_market_review', False)

            try:
                results = pipeline_instance.run(stock_codes=stock_codes, dry_run=args.dry_run, send_notification=not args.no_notify, merge_notification=merge_notification)
            except Exception as pipeline_err:
                logger.error(f"⚠️ 流水线执行中遭遇部分阻碍: {pipeline_err}")

            if config.market_review_enabled and not args.no_market_review and effective_region != '':
                try:
                    from src.core.market_review_lock import try_acquire_market_review_lock, release_market_review_lock
                    lock_token = try_acquire_market_review_lock(config)
                    if lock_token:
                        try:
                            market_report = run_market_review(
                                notifier=pipeline_instance.notifier, analyzer=pipeline_instance.analyzer, search_service=pipeline_instance.search_service,
                                send_notification=not args.no_notify, merge_notification=merge_notification, override_region=effective_region
                            ) or ""
                        finally:
                            release_market_review_lock(lock_token)
                except Exception as review_err:
                    logger.error(f"⚠️ 大盘复盘接口异常: {review_err}")

    except Exception as e:
        logger.exception(f"分析流程前置环节执行失败: {e}")

    # 🔔 ====== 🌟 强制发信兜底逻辑：挪到最外层，排除任何 return 中断 🌟 ======
    if not args.no_notify:
        # 如果前面发生意外没初始化好 pipeline，这里紧急补救一个通知器
        if pipeline_instance is None:
            try:
                pipeline_instance = StockAnalysisPipeline(config=config, max_workers=1, query_id=uuid.uuid4().hex, query_source="cli", save_context_snapshot=False)
            except Exception:
                pass
                
        if pipeline_instance and pipeline_instance.notifier and pipeline_instance.notifier.is_available():
            parts = []
            now_bj = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
            parts.append(f"⏱️ **回执快报**：程序于北京时间 `[{now_bj}]` 运行完毕。")
            
            if not _HAS_REAL_DYNAMIC_DATA:
                parts.append(f"⚠️ **提示**：未获取到目标日 `[{_TARGET_DATE_STR}]` 的实时涨停池，已自动为您转向默认标的。")
            
            if market_report:
                parts.append(f"# 📈 大盘复盘\n\n{market_report}")
            if results:
                dashboard_content = pipeline_instance.notifier.generate_aggregate_report(results, getattr(config, 'report_type', 'simple'))
                parts.append(f"# 🚀 智能推演分析报告\n\n{dashboard_content}")
                
            if len(parts) <= 2 and not results and not market_report:
                parts.append(f"# 📌 流水线状态提示\n\n当前时段未捕获到新增的推演个股或复盘报告，流水线安全空跑收尾。目标日期: {_TARGET_DATE_STR}")
                
            combined_content = "\n\n---\n\n".join(parts)
            logger.info("📧 正在向指定邮箱派发全天候强制汇总回执邮件...")
            try:
                pipeline_instance.notifier.send(combined_content, email_send_to_all=True, route_type="report")
                logger.info("✅ 邮件成功投递至 SMTP 协议栈。")
            except Exception as mail_err:
                logger.error(f"❌ SMTP 邮件投递失败: {mail_err}")
        else:
            logger.warning("⚠️ 通知组件不可用，请检查 .env 的邮箱变量配置！")

def main() -> int:
    args = parse_arguments()
    try:
        logging.basicConfig(level=logging.INFO)
    except Exception:
        pass
    try:
        config = get_config()
        setup_logging(log_prefix="stock_analysis", debug=args.debug, log_dir=config.log_dir)
        run_full_analysis(config, args, None)
    except Exception:
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
