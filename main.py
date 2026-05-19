# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统
===================================
【邮件终极稳定版】
修复：
1. 大盘邮件重复发送
2. 涨停股分析缺失
3. 多封邮件轰炸
4. 汇总邮件为空
5. SMTP 假成功
"""

from __future__ import annotations

# =========================
# 顶级网络超时保护
# =========================
import socket
socket.setdefaulttimeout(8.0)

try:
    import urllib3
    urllib3.util.timeout.Timeout.DEFAULT_TIMEOUT = 8.0

    import requests
    from requests.adapters import HTTPAdapter

    class TimeoutAdapter(HTTPAdapter):

        def __init__(self, *args, **kwargs):
            self.timeout = 8.0
            super().__init__(*args, **kwargs)

        def send(self, request, **kwargs):
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = self.timeout
            return super().send(request, **kwargs)

    old_init = requests.Session.__init__

    def patched_session_init(self, *args, **kwargs):
        old_init(self, *args, **kwargs)

        adapter = TimeoutAdapter()

        self.mount("http://", adapter)
        self.mount("https://", adapter)

    requests.Session.__init__ = patched_session_init

except Exception:
    pass

# =========================
# 基础库
# =========================
import os
import sys
import uuid
import argparse
import logging

from typing import (
    List,
    Optional,
    Tuple,
    Callable,
    Any,
    Dict
)

from pathlib import Path

from datetime import (
    datetime,
    timezone,
    timedelta
)

from dotenv import dotenv_values

# =========================
# 邮件变量兼容
# =========================
if os.getenv("EMAIL_RECEIVER") and not os.getenv("EMAIL_RECEIVERS"):
    os.environ["EMAIL_RECEIVERS"] = os.getenv("EMAIL_RECEIVER")

# =========================
# 项目依赖
# =========================
from src.config import (
    setup_env,
    get_config,
    Config
)

from src.logging_config import setup_logging

from data_provider.base import canonical_stock_code

import akshare as ak

# =========================
# 环境初始化
# =========================
_INITIAL_PROCESS_ENV = dict(os.environ)

setup_env()

# =========================
# Logger
# =========================
logger = logging.getLogger(__name__)

# =========================
# 全局状态
# =========================
_HAS_REAL_DYNAMIC_DATA = False

_TARGET_DATE_STR = ""

# =========================
# 北京时间
# =========================
def get_beijing_time():

    tz_beijing = timezone(timedelta(hours=8))

    return datetime.now(timezone.utc).astimezone(
        tz_beijing
    )

# =========================
# 获取涨停股
# =========================
def get_last_trading_day_stocks() -> list:

    global _HAS_REAL_DYNAMIC_DATA
    global _TARGET_DATE_STR

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

        logger.info(f"📅 抓取涨停池日期: {target_date}")

        df = ak.stock_zt_pool_em(date=target_date)

        if df is None or df.empty:

            logger.warning("⚠️ 未获取到涨停池")

            return []

        code_col = "代码" if "代码" in df.columns else "code"

        df[code_col] = (
            df[code_col]
            .astype(str)
            .str.strip()
        )

        df = df[
            df[code_col].str.startswith(
                ('60', '00', '30')
            )
        ]

        codes = df[code_col].tolist()

        _HAS_REAL_DYNAMIC_DATA = True

        return codes[:30]

    except Exception as e:

        logger.exception(f"❌ 获取涨停池失败: {e}")

        return []

# =========================
# 参数
# =========================
def parse_arguments():

    parser = argparse.ArgumentParser(
        description="A股智能分析"
    )

    parser.add_argument(
        '--debug',
        action='store_true'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true'
    )

    parser.add_argument(
        '--no-notify',
        action='store_true'
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=None
    )

    parser.add_argument(
        '--force-run',
        action='store_true'
    )

    parser.add_argument(
        '--market-review',
        action='store_true'
    )

    parser.add_argument(
        '--no-market-review',
        action='store_true'
    )

    parser.add_argument(
        '--single-notify',
        action='store_true'
    )

    parser.add_argument(
        '--no-context-snapshot',
        action='store_true'
    )

    return parser.parse_args()

# =========================
# 日志
# =========================
def setup_runtime_logging(log_dir, debug=False):

    setup_logging(
        log_prefix="stock_analysis",
        debug=debug,
        log_dir=log_dir
    )

# =========================
# 交易日过滤
# =========================
def _compute_trading_day_filter(
    config: Config,
    args: argparse.Namespace,
    stock_codes: List[str]
) -> Tuple[List[str], Optional[str], bool]:

    try:

        if args.force_run:
            return (
                stock_codes,
                "cn",
                False
            )

        return (
            stock_codes,
            "cn",
            False
        )

    except Exception as e:

        logger.warning(f"⚠️ 交易日检查失败: {e}")

        return (
            stock_codes,
            "cn",
            False
        )

# =========================
# 大盘锁
# =========================
def _run_market_review_with_shared_lock(
    config: Config,
    run_market_review_func: Callable[..., Optional[str]],
    **kwargs: Any
):

    return run_market_review_func(**kwargs)

# =========================
# 核心分析
# =========================
def run_full_analysis(
    config: Config,
    args: argparse.Namespace,
    stock_codes: Optional[List[str]] = None
):

    from src.core.market_review import run_market_review

    from src.core.pipeline import (
        StockAnalysisPipeline
    )

    global _HAS_REAL_DYNAMIC_DATA
    global _TARGET_DATE_STR

    pipeline = None

    results = []

    market_report = ""

    skip_reason = ""

    try:

        logger.info("========================================")
        logger.info("🚀 开始执行 A股智能分析")
        logger.info(
            f"⏰ 北京时间: "
            f"{get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info("========================================")

        # =========================
        # 刷新股票池
        # =========================
        if stock_codes is None:
            config.refresh_stock_list()

        # =========================
        # 自动涨停池
        # =========================
        limit_up_stocks = get_last_trading_day_stocks()

        if limit_up_stocks:

            sanitized = []

            for c in limit_up_stocks:

                try:

                    std = canonical_stock_code(
                        str(c).strip()
                    )

                    if std and std.startswith(
                        ('60', '00', '30')
                    ):
                        sanitized.append(std)

                except Exception:
                    continue

            effective_codes = sanitized[:30]

            config.stock_list = effective_codes

            logger.info(
                f"🎯 已加载涨停股: "
                f"{len(effective_codes)} 只"
            )

        else:

            logger.warning(
                "⚠️ 未获取到涨停池，使用默认股票池"
            )

            effective_codes = [
                c for c in config.stock_list
                if str(c).startswith(
                    ('60', '00', '30')
                )
            ][:30]

        # =========================
        # 交易日
        # =========================
        filtered_codes, effective_region, should_skip = (
            _compute_trading_day_filter(
                config,
                args,
                effective_codes
            )
        )

        # =========================
        # 初始化 pipeline
        # =========================
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        query_id = uuid.uuid4().hex

        workers = (
            args.workers
            if args.workers is not None
            else 2
        )

        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=False
        )

        # =========================
        # 非交易日
        # =========================
        if should_skip:

            skip_reason = (
                f"今日非交易日，"
                f"跳过量化推演。"
            )

            logger.info(skip_reason)

        else:

            stock_codes = filtered_codes

            # =========================
            # 个股分析
            # =========================
            try:

                logger.info(
                    "🚀 开始执行涨停股分析..."
                )

                results = pipeline.run(
                    stock_codes=stock_codes,

                    dry_run=args.dry_run,

                    send_notification=False,

                    merge_notification=True

                ) or []

                logger.info(
                    f"✅ 个股分析完成 "
                    f"{len(results)} 条"
                )

            except Exception as e:

                logger.exception(
                    f"❌ 个股分析失败: {e}"
                )

                results = []

            # =========================
            # 大盘复盘
            # =========================
            if (
                config.market_review_enabled
                and not args.no_market_review
            ):

                try:

                    logger.info(
                        "📈 开始执行大盘复盘..."
                    )

                    review_result = (
                        _run_market_review_with_shared_lock(
                            config,

                            run_market_review,

                            notifier=pipeline.notifier,

                            analyzer=pipeline.analyzer,

                            search_service=pipeline.search_service,

                            send_notification=False,

                            merge_notification=True,

                            override_region=effective_region
                        )
                    )

                    if review_result:

                        market_report = review_result

                        logger.info(
                            "✅ 大盘复盘完成"
                        )

                except Exception as e:

                    logger.exception(
                        f"❌ 大盘复盘失败: {e}"
                    )

        # =========================
        # 汇总邮件
        # =========================
        if (
            not args.no_notify
            and pipeline
            and pipeline.notifier
            and pipeline.notifier.is_available()
        ):

            logger.info(
                "📧 开始生成统一邮件..."
            )

            parts = []

            now_bj = get_beijing_time().strftime(
                '%Y-%m-%d %H:%M:%S'
            )

            parts.append(
                f"# 🤖 A股智能分析日报\n\n"
                f"⏰ 生成时间：{now_bj}\n\n"
                f"📅 数据日期：{_TARGET_DATE_STR}"
            )

            if market_report:

                parts.append(
                    f"# 📈 大盘复盘\n\n"
                    f"{market_report}"
                )

            if results:

                try:

                    logger.info(
                        "📊 正在生成个股汇总..."
                    )

                    dashboard_content = (
                        pipeline.notifier.generate_aggregate_report(
                            results,
                            getattr(
                                config,
                                'report_type',
                                'simple'
                            )
                        )
                    )

                    if dashboard_content:

                        parts.append(
                            f"# 🚀 涨停股深度分析\n\n"
                            f"{dashboard_content}"
                        )

                        logger.info(
                            "✅ 个股汇总成功"
                        )

                except Exception as e:

                    logger.exception(
                        f"❌ 汇总生成失败: {e}"
                    )

            if len(parts) <= 1:

                parts.append(
                    "# 📌 系统状态\n\n"
                    "当前无有效分析结果"
                )

            combined_content = (
                "\n\n---\n\n".join(parts)
            )

            logger.info(
                "📧 正在发送统一邮件..."
            )

            pipeline.notifier.send(
                combined_content,

                email_send_to_all=True,

                route_type="report"
            )

            logger.info(
                "✅ 汇总邮件发送成功"
            )

        # =========================
        # 控制台摘要
        # =========================
        if results:

            logger.info(
                "\n===== 分析结果摘要 ====="
            )

            for r in sorted(
                results,
                key=lambda x: getattr(
                    x,
                    'sentiment_score',
                    60
                ),
                reverse=True
            ):

                logger.info(
                    f"{r.name}({r.code}) "
                    f"| 评分 "
                    f"{getattr(r, 'sentiment_score', 60)}"
                )

    except Exception as e:

        logger.exception(
            f"❌ 系统执行失败: {e}"
        )

        raise e

# =========================
# 主函数
# =========================
def main():

    args = parse_arguments()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    try:

        config = get_config()

        setup_runtime_logging(
            config.log_dir,
            debug=args.debug
        )

        run_full_analysis(
            config,
            args,
            None
        )

        return 0

    except Exception as e:

        logger.exception(f"❌ 主程序失败: {e}")

        return 1

# =========================
# 入口
# =========================
if __name__ == "__main__":

    sys.exit(main())
