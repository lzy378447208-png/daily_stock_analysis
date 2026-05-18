# -*- coding: utf-8 -*-
"""
===================================
趋势交易分析器 - 基于用户交易理念 & AI 强题材赋能
【优化版：昨日涨停板专用短线情绪量化分析】
===================================

交易理念核心原则：
1. 稳健策略 - 普通股不追高，MA5>MA10>MA20 且乖离率 < 5% 附近回踩买入。
2. 游资龙头策略 - 针对昨日涨停股：打破常规乖离率限制，重点量化连板高度、封单强度，由 AI 深度剖析题材持续性。
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

import pandas as pd
import numpy as np

from src.config import get_config

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    """趋势状态枚举"""
    STRONG_BULL = "强势多头"      # 涨停或多头且间距扩大
    BULL = "多头排列"             # MA5 > MA10 > MA20
    WEAK_BULL = "弱势多头"         # 均线多头但价格跌破MA5
    SHOCK = "震荡平稳"             # 均线交织
    BEAR = "空头排列"             # 均线向下


@dataclass
class TrendAnalysisResult:
    """趋势分析结果数据结构"""
    code: str
    name: str
    status: TrendStatus
    sentiment_score: int = 50       # 综合情绪评分 (0-100)
    operation_advice: str = "观望"  # 买入/卖出/观望
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    ai_analysis_report: str = ""   # AI 题材龙虎榜复盘报告
    
    # 扩展涨停特征字段
    is_limit_up: bool = False
    limit_up_type: str = "首板"     # 首板/连板
    font_money: str = "未知"        # 封单资金


class StockTrendAnalyzer:
    """针对A股短线与技术面融合的智能分析器"""

    def __init__(self):
        self.config = get_config()

    def analyze(self, df: pd.DataFrame, code: str, is_limit_up_pool: bool = True) -> TrendAnalysisResult:
        """
        核心分析函数
        :param df: 包含股票历史K线数据的 DataFrame (包含 close, open, high, low, volume, ma5, ma10, ma20 等)
        :param code: 股票代码
        :param is_limit_up_pool: 是否属于昨日涨停池中的标的（默认开启拦截）
        """
        stock_name = df['name'].iloc[-1] if 'name' in df.columns else "未知股票"
        
        # 基础数据提取
        close_p = df['close'].iloc[-1]
        ma5 = df['ma5'].iloc[-1] if 'ma5' in df.columns else close_p
        ma10 = df['ma10'].iloc[-1] if 'ma10' in df.columns else close_p
        ma20 = df['ma20'].iloc[-1] if 'ma20' in df.columns else close_p
        
        # 1. 动态计算趋势状态
        if ma5 > ma10 > ma20:
            status = TrendStatus.STRONG_BULL if is_limit_up_pool else TrendStatus.BULL
        elif ma5 > ma20 and ma10 > ma20:
            status = TrendStatus.WEAK_BULL
        elif ma5 < ma10 < ma20:
            status = TrendStatus.BEAR
        else:
            status = TrendStatus.SHOCK

        # 初始化结果
        result = TrendAnalysisResult(
            code=code,
            name=stock_name,
            status=status,
            is_limit_up=is_limit_up_pool
        )

        # 2. 核心量化评估与规则拦截
        score = 60  # 基准分
        bias_5 = (close_p - ma5) / ma5 if ma5 else 0

        if is_limit_up_pool:
            # ================== 涨停板游资情绪量化逻辑 ==================
            result.signal_reasons.append("🔥 标的属于昨日涨停股，具备极强的短线资金关注度与赚钱效应。")
            score += 15
            
            # 评估均线形态对短线爆发力的支撑
            if ma5 > ma10:
                result.signal_reasons.append("均线维持攻击形态（MA5 > MA10），短线动能充沛。")
                score += 10
            else:
                result.risk_factors.append("虽然昨日涨停，但短期均线未完全理顺，防范冲高回落风险。")
                score -= 5
                
            # 对涨停股放宽乖离率，进行警告提示而非一刀切拒绝买入
            if bias_5 > 0.06:
                result.risk_factors.append(f"当前价格偏离5日线较远 (乖离率 {bias_5:.1%})，追高需谨慎，更佳买点在回踩5日线或分时分歧时。")
                result.operation_advice = "博弈/分歧买入"
            else:
                result.signal_reasons.append(f"价格距离5日线位置合理 (乖离率 {bias_5:.1%})，具备安全边际。")
                result.operation_advice = "买入"
        else:
            # ================== 普通自选股稳健趋势交易逻辑 ==================
            if ma5 > ma10 > ma20:
                result.signal_reasons.append("技术面呈现标准多头排列，趋势向上。")
                score += 10
            
            # 严格拦截高乖离率（防止追高）
            if bias_5 >= 0.05:
                result.risk_factors.append(f"价格短线涨幅过急，乖离率({bias_5:.1%})超过5%预警线，拒绝追高。")
                score -= 20
                result.operation_advice = "观望"
            elif 0 <= bias_5 < 0.02:
                result.signal_reasons.append(f"精准回踩/贴近5日均线(乖离率{bias_5:.1%})，触发效率买点。")
                score += 15
                result.operation_advice = "买入"
            else:
                result.operation_advice = "观望"

        # 限制分数边界
        result.sentiment_score = max(0, min(100, score))
        
        # 3. 构造传递给 Gemini 的短线游资专用 Prompt 框架
        result.ai_analysis_report = self._generate_ai_prompt(df, result)
        
        return result

    def _generate_ai_prompt(self, df: pd.DataFrame, res: TrendAnalysisResult) -> str:
        """为大模型量身定制的题材热点及连板潜力 Prompt"""
        last_row = df.iloc[-1]
        
        prompt = f"""
        请作为A股短线游资操盘手，重点针对昨日【涨停板】个股进行深度技术面与题材面推演：
        
        【个股基本面 & 核心量化指标】:
        - 股票代码: {res.code} | 名称: {res.name}
        - 当前价格: {last_row.get('close', 'N/A')} | 涨跌幅: {last_row.get('pct_chg', 'N/A')}%
        - 均线排列: {res.status.value} | 5日乖离率: {(last_row.get('close', 0) - last_row.get('ma5', 0)) / last_row.get('ma5', 1):.2%}
        - 当日成交额: {last_row.get('amount', 'N/A')} 元 | 换手率: {last_row.get('turnover_rate', 'N/A')}%
        
        【技术面自检标签】:
        - 优势: {", ".join(res.signal_reasons) if res.signal_reasons else "暂无"}
        - 风险: {", ".join(res.risk_factors) if res.risk_factors else "暂无"}
        
        【深度分析任务要求】:
        1. 寻找核心热点催化剂：根据最新的市场舆情和公告，挖出该股昨日涨停的核心题材是什么（如固态电池、低空经济、AI等），是否属于当前市场绝对主线？
        2. 评估连板空间与资金态度：结合该股的换手率和成交额，分析它是缩量一字板（买不到）、分时弱转强爆量板、还是跟风板？今天是否有连板或者溢价晋级可能？
        3. 给出次日具体的临盘应对策略：如果明天开盘大幅冲高如何应对？如果回踩5日线、10日线应该在什么点位低吸接力？
        """
        return prompt


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    analyzer = StockTrendAnalyzer()
    # 默认作为涨停池标的处理
    return analyzer.analyze(df, code, is_limit_up_pool=True)


if __name__ == "__main__":
    # 测试桩
    logging.basicConfig(level=logging.INFO)
    logger.info("测试优化版昨日涨停股专项分析模块...")
    
    # 构建模拟涨停K线
    dates = pd.date_range(start='2026-05-01', periods=5, freq='D')
    df_test = pd.DataFrame({
        'name': ['龙头股份'] * 5,
        'close': [10.0, 10.5, 11.0, 11.5, 12.65],  # 最后一根大阳线/涨停
        'ma5': [9.8, 10.1, 10.4, 10.8, 11.2],
        'ma10': [9.5, 9.7, 10.0, 10.2, 10.5],
        'ma20': [9.0, 9.2, 9.4, 9.6, 9.8],
        'pct_chg': [2.1, 5.0, 4.7, 4.5, 10.0],
        'amount': [120000000] * 5,
        'turnover_rate': [4.5] * 5
    }, index=dates)
    
    res = analyze_stock(df_test, "600001")
    print(f"\n量化得分: {res.sentiment_score} | 建议: {res.operation_advice}")
    print(res.ai_analysis_report)
