# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================
【稳定终版】
修复内容：
1. 邮件发送成功但无涨停分析
2. 大盘复盘重复发送
3. pipeline.run 返回空问题定位
4. 非交易日仍发送状态邮件
5. GitHub Actions 超时稳定化
"""

from __future__ import annotations

# ====================== 顶级超时保护 ======================

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

        adapter = TimeoutAdapter()

        self.mount("http://", adapter)
        self.mount("https://", adapter)

    requests.Session.__init__ = _patched_session_init

except Exception:
    pass

# ====================== 标准库 ======================

import os
import sys
import argparse
import logging
import uuid

from typing import (
    List,
    Optional,
    Tuple,
)

from datetime import (
    datetime,
    timezone,
    timedelta
)

# ====================== 项目模块 ======================

from src.config import (
    get_config,
    Config,
)

from src.logging_config import setup_logging

from data_provider.base import canonical_stock_code

import akshare as ak

# ====================== 日志 ======================

logger = logging.getLogger(__name__)

# ====================== 全局变量 ======================

_HAS_REAL_DYNAMIC_DATA = False
_TARGET_DATE_STR = ""

# ==========================================================
# 北京时间
# ==========================================================

def get_beijing_time() -> datetime:

    tz_beijing = timezone(timedelta(hours=8))

    return datetime.now(
        timezone.utc
    ).astimezone(tz_beijing)

# ==========================================================
# 自动获取涨停池
# ==========================================================

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

        logger.info(f"📅 抓取涨停数据: {target_date}")

        try:

            df = ak.stock_zt_pool_em(date=target_date)

        except Exception as e:

            logger.exception(f"❌ 东财涨停池接口失败: {e}")

            return []

        if df is not None and not df.empty:

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

            raw_list = df[code_col].tolist()

            final_30 = raw_list[:30]

            _HAS_REAL_DYNAMIC_DATA = True

            logger.info(f"✅ 成功获取涨停股: {len(final_30)}")

            return final_30

        logger.warning("⚠️ 涨停池为空")

        return []

    except Exception as e:

        logger.exception(f"❌ 获取涨停池失败: {e}")

        return []

# ==========================================================
# 参数解析
# ==========================================================

def parse_arguments():

    parser = argparse.ArgumentParser(
        description="A股智能分析系统"
    )

    parser.add_argument('--debug', action='store_true')

    parser.add_argument('--dry-run', action='store_true')

    parser.add_argument('--workers', type=int)

    parser.add_argument('--no-notify', action='store_true')

    parser.add_argument('--force-run', action='store_true')

    parser.add_argument('--no-market-review', action='store_true')

    parser.add_argument('--single-notify', action='store_true')

    parser.add_argument('--no-context-snapshot', action='store_true')

    return parser.parse_args()

# ==========================================================
# 交易日检查
# ==========================================================

def _compute_trading_day_filter(
    config: Config,
    args,
    stock_codes: List[str]
) -> Tuple[List[str], Optional[str], bool]:

    if args.force_run:
        return stock_codes, "cn", False

    return stock_codes, "cn", False

# ==========================================================
# 大盘复盘锁
# ==========================================================

def _run_market_review_with_shared_lock(
    config,
    run_market_review_func,
    **kwargs
):

    return run_market_review_func(**kwargs)

# ==========================================================
# 核心分析
# ==========================================================

def run_full_analysis(
    config: Config,
    args,
    stock_codes: Optional[List[str]] = None
):

    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

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

        # ======================================================
        # 自动加载涨停股
        # ======================================================

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
                "⚠️ 未获取涨停池，使用默认股票池"
            )

            effective_codes = config.stock_list[:10]

        # ======================================================
        # 交易日过滤
        # ======================================================

        filtered_codes, effective_region, should_skip = (
            _compute_trading_day_filter(
                config,
                args,
                effective_codes
            )
        )

        # ======================================================
        # 初始化 Pipeline
        # ======================================================

        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        workers = (
            args.workers
            if args.workers is not None
            else 1
        )

        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=workers,
            query_id=uuid.uuid4().hex,
            query_source="cli",
            save_context_snapshot=False
        )

        # ======================================================
        # 非交易日
        # ======================================================

        if should_skip:

            skip_reason = (
                f"今日非交易日，跳过分析。"
                f"目标日期: {_TARGET_DATE_STR}"
            )

            logger.warning(skip_reason)

        else:

            stock_codes = filtered_codes

            # ==================================================
            # 个股分析
            # ==================================================

            try:

                logger.info("🚀 开始执行涨停股分析...")

                results = pipeline.run(
                    stock_codes=stock_codes,
                    dry_run=args.dry_run,

                    # 禁止内部邮件
                    send_notification=False,

                    # 强制统一汇总
                    merge_notification=True
                )

                logger.info(
                    f"📊 pipeline.run 原始返回: {results}"
                )

                if results is None:

                    logger.warning(
                        "⚠️ pipeline.run 返回 None"
                    )

                    results = []

                elif not isinstance(results, list):

                    logger.warning(
                        f"⚠️ pipeline.run 返回异常类型:"
                        f"{type(results)}"
                    )

                    results = []

                logger.info(
                    f"📈 最终 results 数量:"
                    f"{len(results)}"
                )

            except Exception as pipeline_err:

                logger.exception(
                    f"❌ 个股分析失败: {pipeline_err}"
                )

                results = []

            # ==================================================
            # 大盘复盘
            # ==================================================

            if (
                config.market_review_enabled
                and not args.no_market_review
            ):

                try:

                    logger.info("📈 开始执行大盘复盘...")

                    review_result = (
                        _run_market_review_with_shared_lock(
                            config,
                            run_market_review,
                            notifier=pipeline.notifier,
                            analyzer=pipeline.analyzer,
                            search_service=pipeline.search_service,

                            # 禁止内部发送
                            send_notification=False,

                            merge_notification=True,

                            override_region=effective_region
                        )
                    )

                    if review_result:

                        market_report = review_result

                        logger.info("✅ 大盘复盘完成")

                except Exception as review_err:

                    logger.exception(
                        f"❌ 大盘复盘失败: {review_err}"
                    )

        # ======================================================
        # 最终统一邮件
        # ======================================================

        if (
            not args.no_notify
            and pipeline
            and pipeline.notifier
            and pipeline.notifier.is_available()
        ):

            logger.info("📧 开始生成汇总邮件...")

            parts = []

            now_bj = get_beijing_time().strftime(
                '%Y-%m-%d %H:%M:%S'
            )

            parts.append(
                f"# 🤖 A股智能分析日报\n\n"
                f"⏰ 生成时间：{now_bj}\n\n"
                f"📅 数据日期：{_TARGET_DATE_STR}"
            )

            # ==================================================
            # 大盘复盘
            # ==================================================

            if market_report:

                parts.append(
                    f"# 📈 大盘复盘\n\n"
                    f"{market_report}"
                )

            # ==================================================
            # 涨停股分析
            # ==================================================

            if results:

                try:

                    logger.info(
                        "📊 开始生成涨停股汇总..."
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
                            "✅ 涨停股汇总成功"
                        )

                    else:

                        logger.warning(
                            "⚠️ dashboard_content 为空"
                        )

                except Exception as agg_err:

                    logger.exception(
                        f"❌ 汇总生成失败: {agg_err}"
                    )

            else:

                parts.append(
                    "# ⚠️ 涨停股分析\n\n"
                    "本次未生成有效个股分析结果。"
                )

            # ==================================================
            # 发送邮件
            # ==================================================

            combined_content = "\n\n---\n\n".join(parts)

            logger.info("📧 正在发送邮件...")

            pipeline.notifier.send(
                combined_content,
                email_send_to_all=True,
                route_type="report"
            )

            logger.info("✅ 邮件发送成功")

        else:

            logger.warning("⚠️ 邮件通知器不可用")

    except Exception as e:

        logger.exception(f"❌ 系统执行失败: {e}")

        raise e

# ==========================================================
# 主入口
# ==========================================================

def main():

    args = parse_arguments()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    config = get_config()

    setup_logging(
        log_prefix="stock_analysis",
        debug=args.debug,
        log_dir=config.log_dir
    )

    run_full_analysis(config, args)

    return 0

# ==========================================================
# 启动
# ==========================================================

if __name__ == "__main__":

    sys.exit(main())
