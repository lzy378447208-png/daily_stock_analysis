# -*- coding: utf-8 -*-
"""
===================================
趋势交易分析器 - 涨停板分时形态量化尊享版
===================================
[第3点功能实现]：深度解析炸板次数、封板时间，精准量化龙头股的封板质量。
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import pandas as pd

from src.config import get_config

logger = logging.getLogger(__name__)

class TrendStatus(Enum):
    STRONG_BULL = "强势游资多头"
    BULL = "多头排列"
    WEAK_BULL = "弱势多头"
    SHOCK = "震荡平稳"
    BEAR = "空头排列"

@dataclass
class TrendAnalysisResult:
    code: str
    name: str
    status: TrendStatus
    sentiment_score: int = 50
    operation_advice: str = "观望"
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    ai_analysis_report: str = ""

class StockTrendAnalyzer:
    def __init__(self):
        self.config = get_config()

    def analyze(self, df: pd.DataFrame, code: str) -> TrendAnalysisResult:
        """
        核心量化评估：自动融入天梯图缓存中的分时数据进行双重过滤
        """
        stock_name = df['name'].iloc[-1] if 'name' in df.columns else "未知股票"
        close_p = df['close'].iloc[-1]
        
        ma5 = df['ma5'].iloc[-1] if 'ma5' in df.columns else close_p
        ma10 = df['ma10'].iloc[-1] if 'ma10' in df.columns else close_p
        ma20 = df['ma20'].iloc[-1] if 'ma20' in df.columns else close_p

        # 1. 均线多头基础判定
        if ma5 > ma10 > ma20:
            status = TrendStatus.STRONG_BULL
        else:
            status = TrendStatus.SHOCK

        result = TrendAnalysisResult(code=code, name=stock_name, status=status)
        score = 60  # 基础分

        # 2. 跨模块读取 main.py 缓存的专属短线指标 [第3点核心逻辑]
        from main import GLOBAL_LIMIT_UP_DETAIL_MAP
        
        # 判断当前股票是否在昨日涨停池里
        if code in GLOBAL_LIMIT_UP_DETAIL_MAP:
            info = GLOBAL_LIMIT_UP_DETAIL_MAP[code]
            
            result.signal_reasons.append(f"🔥 昨日触发涨停：属于【{info['reason']}】题材，连板高度为 【{info['status_text']}】")
            score += 10

            # --- 分时封板质量量化扣分/加分机制 ---
            first_time_str = info["first_time"] # 例: "09:32:15"
            open_count = info["open_count"]     # 例: 2
            
            # (A) 评估封板时间
            try:
                time_parts = first_time_str.split(":")
                hour, minute = int(time_parts[0]), int(time_parts[1])
                
                if hour == 9 and minute <= 40:
                    result.signal_reasons.append(f"⚡ 早盘狂飙：{first_time_str} 极其迅速完成封板，主力态度极其坚决，属于超预期极强标的。")
                    score += 15
                elif hour >= 14:
                    result.risk_factors.append(f"⚠️ 尾盘偷袭：封板时间较晚 ({first_time_str})，防范主力资金由于信心不足在次日诱多套人。")
                    score -= 10
            except Exception:
                pass

            # (B) 评估炸板分歧次数
            if open_count == 0:
                result.signal_reasons.append("💎 零炸板一气呵成：全天未曾开板（一字或死封），筹码结构极其锁定。")
                score += 10
            elif open_count >= 3:
                result.risk_factors.append(f"⚠️ 分歧巨大（烂板）：全天开板/炸板共 {open_count} 次，场内资金分歧剧烈，洗盘未结束前谨防大幅低开。")
                score -= 15

            result.operation_advice = "博弈龙头接力" if score >= 70 else "逢高分步止盈"
            
        else:
            # 普通非涨停自选股逻辑：保持原有回踩均线买入策略
            bias_5 = (close_p - ma5) / ma5 if ma5 else 0
            if bias_5 >= 0.05:
                result.risk_factors.append("拒绝追高：短线乖离率超过 5%，需等回踩。")
                score -= 15
                result.operation_advice = "观望"
            elif 0 <= bias_5 < 0.02:
                result.signal_reasons.append("触发买点：均线多头且精准回踩 5 日均线附近。")
                score += 15
                result.operation_advice = "关注买入"
            else:
                result.operation_advice = "观望"

        result.sentiment_score = max(0, min(100, score))
        
        # 3. 将洗好的分时指标拼装成高级 Prompt 发送给 Gemini
        result.ai_analysis_report = self._build_gemini_prompt(df, result, code)
        return result

    def _build_gemini_prompt(self, df: pd.DataFrame, res: TrendAnalysisResult, code: str) -> str:
        from main import GLOBAL_LIMIT_UP_DETAIL_MAP
        last_row = df.iloc[-1]
        
        # 获取分时详情用于 Prompt 增强
        info = GLOBAL_LIMIT_UP_DETAIL_MAP.get(code, {
            "reason": "趋势个股", "status_text": "常规形态", 
            "first_time": "未知", "open_count": 0, "font_money": "未知"
        })

        prompt = f"""
        请作为顶级顶级游资、短线总舵主，结合个股的【短线题材天梯位】与【分时封板质量】进行深度复盘：
        
        【个股短线特征卡】:
        - 代码/名称: {res.code} | {res.name}
        - 核心题材主线: {info['reason']}
        - 当前连板梯队: {info['status_text']} (高度分值: {info.get('height', 1)})
        - 首次封板时间: {info['first_time']} | 全天开板次数: {info['open_count']} 次
        - 封单资金总额: {info['font_money']}
        
        【技术量化自检报告】:
        - 量化得分: {res.sentiment_score} 分 | 趋势形态: {res.status.value}
        - 买入优势: {", ".join(res.signal_reasons) if res.signal_reasons else "暂无"}
        - 潜在隐患: {", ".join(res.risk_factors) if res.risk_factors else "暂无"}
        
        【顶级复盘推演任务】:
        1. 【封板含金量判定】：根据首次封板时间和炸板次数，客观评价该股属于“缩量主力惜售板”、“爆量强行洗盘板”还是“弱势跟风偷袭板”？
        2. 【题材持续力评估】：该股所处的连板天梯位置（如龙头还是小弟），在明天的板块轮动中是否具备抵抗炸板的分离度？
        3. 【次日临盘推演】：根据今晚的封单强度，预测明日竞价开盘的涨幅预期是多少？给出如果开盘高开 >5% 或是低开超预期的具体接力/出局操盘战术。
        """
        return prompt

def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    analyzer = StockTrendAnalyzer()
    return analyzer.analyze(df, code)
