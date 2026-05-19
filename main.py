# =====================================================================
# 📧 🔥【修复后的 run_full_analysis 完整版】
# 解决：
# 1. 大盘邮件重复发送
# 2. 涨停股分析不进入邮件
# 3. 多封邮件轰炸
# 4. 汇总邮件缺失
# =====================================================================

def run_full_analysis(config: Config, args: argparse.Namespace, stock_codes: Optional[List[str]] = None):
    from src.core.market_review import run_market_review
    from src.core.pipeline import StockAnalysisPipeline

    global _HAS_REAL_DYNAMIC_DATA, _TARGET_DATE_STR

    pipeline = None
    results = []
    market_report = ""
    skip_reason = ""

    try:

        # ==========================================================
        # 刷新股票池
        # ==========================================================
        if stock_codes is None:
            config.refresh_stock_list()

        # ==========================================================
        # 自动获取涨停股
        # ==========================================================
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

            logger.warning("⚠️ 未获取到涨停池，使用默认股票池")

            effective_codes = [
                c for c in config.stock_list
                if str(c).startswith(('60', '00', '30'))
            ][:30]

        # ==========================================================
        # 交易日过滤
        # ==========================================================
        filtered_codes, effective_region, should_skip = _compute_trading_day_filter(
            config,
            args,
            effective_codes
        )

        # ==========================================================
        # 初始化 Pipeline
        # ==========================================================
        if getattr(args, 'single_notify', False):
            config.single_stock_notify = True

        save_context_snapshot = False if getattr(args, 'no_context_snapshot', False) else None

        query_id = uuid.uuid4().hex

        workers = args.workers if args.workers is not None else 2

        pipeline = StockAnalysisPipeline(
            config=config,
            max_workers=workers,
            query_id=query_id,
            query_source="cli",
            save_context_snapshot=save_context_snapshot
        )

        # ==========================================================
        # 非交易日
        # ==========================================================
        if should_skip:

            skip_reason = (
                f"今日判断为非交易日（休市），"
                f"系统自动跳过核心量化推演。"
                f"数据目标日：{_TARGET_DATE_STR}"
            )

            logger.info(skip_reason)

        else:

            stock_codes = filtered_codes

            # ======================================================
            # 🚨 个股分析（禁止内部邮件发送）
            # ======================================================
            try:

                logger.info("🚀 开始执行涨停股智能分析...")

                results = pipeline.run(
                    stock_codes=stock_codes,

                    dry_run=args.dry_run,

                    # 🚨 核心修复：
                    # 禁止 pipeline 内部发送邮件
                    send_notification=False,

                    # 🚨 强制统一汇总
                    merge_notification=True

                ) or []

                logger.info(f"✅ 个股分析完成，共生成 {len(results)} 条结果")

            except Exception as pipeline_err:

                logger.exception(
                    f"⚠️ 个股分析流水线异常: {pipeline_err}"
                )

                results = []

            # ======================================================
            # 🚨 大盘复盘（禁止内部邮件发送）
            # ======================================================
            if (
                config.market_review_enabled
                and not args.no_market_review
                and effective_region != ''
            ):

                try:

                    logger.info("📈 开始执行大盘复盘分析...")

                    review_result = _run_market_review_with_shared_lock(
                        config,

                        run_market_review,

                        notifier=pipeline.notifier,

                        analyzer=pipeline.analyzer,

                        search_service=pipeline.search_service,

                        # 🚨 核心修复：
                        # 禁止内部发送
                        send_notification=False,

                        # 🚨 强制统一汇总
                        merge_notification=True,

                        override_region=effective_region
                    )

                    if review_result:
                        market_report = review_result

                        logger.info("✅ 大盘复盘完成")

                except Exception as review_err:

                    logger.exception(
                        f"⚠️ 大盘复盘异常: {review_err}"
                    )

        # ==========================================================
        # 🚨 最终统一邮件发送（只发送一次）
        # ==========================================================
        if (
            not args.no_notify
            and pipeline
            and pipeline.notifier
            and pipeline.notifier.is_available()
        ):

            logger.info("📧 开始生成统一汇总邮件...")

            parts = []

            now_bj = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')

            # ======================================================
            # 系统状态
            # ======================================================
            parts.append(
                f"# 🤖 A股智能分析日报\n\n"
                f"⏰ 生成时间：{now_bj}\n\n"
                f"📅 数据目标日：{_TARGET_DATE_STR}"
            )

            # ======================================================
            # 非交易日提示
            # ======================================================
            if skip_reason:

                parts.append(
                    f"# ℹ️ 运行状态\n\n"
                    f"{skip_reason}"
                )

            # ======================================================
            # 涨停数据状态
            # ======================================================
            elif not _HAS_REAL_DYNAMIC_DATA:

                parts.append(
                    "# ⚠️ 数据提示\n\n"
                    "未获取到实时涨停池，系统已自动切换至默认股票池分析。"
                )

            # ======================================================
            # 大盘复盘
            # ======================================================
            if market_report:

                parts.append(
                    f"# 📈 大盘复盘\n\n"
                    f"{market_report}"
                )

            # ======================================================
            # 个股分析（核心修复）
            # ======================================================
            if results:

                logger.info("📊 正在生成个股汇总报告...")

                try:

                    dashboard_content = (
                        pipeline.notifier.generate_aggregate_report(
                            results,
                            getattr(config, 'report_type', 'simple')
                        )
                    )

                    if dashboard_content:

                        parts.append(
                            f"# 🚀 涨停股深度分析\n\n"
                            f"{dashboard_content}"
                        )

                        logger.info("✅ 个股汇总报告生成成功")

                    else:

                        logger.warning("⚠️ generate_aggregate_report 返回为空")

                except Exception as agg_err:

                    logger.exception(
                        f"⚠️ 汇总报告生成失败: {agg_err}"
                    )

            # ======================================================
            # 空报告兜底
            # ======================================================
            if len(parts) <= 2:

                parts.append(
                    "# 📌 系统状态\n\n"
                    "当前无可推演数据，分析系统已安全结束运行。"
                )

            # ======================================================
            # 合并最终邮件
            # ======================================================
            combined_content = "\n\n---\n\n".join(parts)

            logger.info("📧 正在发送统一汇总邮件...")

            try:

                pipeline.notifier.send(
                    combined_content,

                    email_send_to_all=True,

                    route_type="report"
                )

                logger.info("✅ 汇总邮件发送成功")

            except Exception as mail_err:

                logger.exception(
                    f"❌ 邮件发送失败: {mail_err}"
                )

                raise mail_err

        else:

            logger.warning("⚠️ 邮件通知器不可用，跳过发送")

        # ==========================================================
        # 控制台摘要
        # ==========================================================
        if results:

            logger.info("\n===== 分析结果摘要 =====")

            for r in sorted(
                results,
                key=lambda x: getattr(x, 'sentiment_score', 60),
                reverse=True
            ):

                emoji = getattr(r, 'get_emoji', lambda: "🔍")()

                score = getattr(
                    r,
                    'sentiment_score',
                    getattr(r, 'signal_score', 60)
                )

                advice = getattr(
                    r,
                    'operation_advice',
                    '观望'
                )

                logger.info(
                    f"{emoji} {r.name}({r.code}) "
                    f"| {advice} "
                    f"| 评分 {score}"
                )

        # ==========================================================
        # 飞书文档同步
        # ==========================================================
        try:

            from src.feishu_doc import FeishuDocManager

            feishu_doc = FeishuDocManager()

            if feishu_doc.is_configured() and (results or market_report):

                tz_cn = timezone(timedelta(hours=8))

                now = datetime.now(tz_cn)

                doc_title = (
                    f"{now.strftime('%Y-%m-%d')} "
                    f"A股量化复盘"
                )

                full_content = ""

                if market_report:

                    full_content += (
                        f"# 📈 大盘复盘\n\n"
                        f"{market_report}\n\n---\n\n"
                    )

                if results:

                    dashboard_content = (
                        pipeline.notifier.generate_aggregate_report(
                            results,
                            getattr(config, 'report_type', 'simple')
                        )
                    )

                    full_content += (
                        f"# 🚀 涨停股深度分析\n\n"
                        f"{dashboard_content}"
                    )

                doc_url = feishu_doc.create_daily_doc(
                    doc_title,
                    full_content
                )

                if doc_url and not args.no_notify:

                    pipeline.notifier.send(
                        f"📄 飞书复盘文档已生成：\n\n{doc_url}",
                        route_type="report"
                    )

        except Exception as e:

            logger.exception(f"飞书文档生成失败：{e}")

    except Exception as e:

        logger.exception(f"❌ 系统执行失败：{e}")

        raise e
