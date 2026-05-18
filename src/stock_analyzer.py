# -*- coding: utf-8 -*-
"""
===================================
趋势交易分析器 - 基于用户交易理念 & AI 强题材赋能
【精雕细琢版：融合封板质量与分时特征】
===================================
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum

import pandas as pd
import numpy as np

from src.config import get_config

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    STRONG_BULL = "强势多头"
    BULL = "多头排列"
    WEAK_BULL = "弱势多头"
    CONSOLIDATION = "盘整"
    WEAK_BEAR = "弱势空头"
    BEAR = "空头排列"
    STRONG_BEAR = "强势空头"


class VolumeStatus(Enum):
    HEAVY_VOLUME_UP = "放量上涨"
    HEAVY_VOLUME_DOWN = "放量下跌"
    SHRINK_VOLUME_UP = "缩量上涨"
    SHRINK_VOLUME_DOWN = "缩量回调"
    NORMAL = "量能正常"


class BuySignal(Enum):
    STRONG_BUY = "强烈买入"
    BUY = "买入"
    HOLD = "持有"
    WAIT = "观望"
    SELL = "卖出"
    STRONG_SELL = "强烈卖出"


class MACDStatus(Enum):
    GOLDEN_CROSS_ZERO = "零轴上金叉"
    GOLDEN_CROSS = "金叉"
    BULLISH = "多头"
    CROSSING_UP = "上穿零轴"
    CROSSING_DOWN = "下穿零轴"
    BEARISH = "空头"
    DEATH_CROSS = "死叉"


class RSIStatus(Enum):
    OVERBOUGHT = "超买"
    STRONG_BUY = "强势买入"
    NEUTRAL = "中性"
    WEAK = "弱势"
    OVERSOLD = "超卖"


@dataclass
class TrendAnalysisResult:
    code: str
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""
    support_ma5: bool = False
    support_ma10: bool = False
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    is_limit_up: bool = False
    limit_up_reason: str = ""
    ai_analysis_report: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'trend_status': self.trend_status.value,
            'ma_alignment': self.ma_alignment,
            'trend_strength': self.trend_strength,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'current_price': self.current_price,
            'bias_ma5': self.bias_ma5,
            'bias_ma10': self.bias_ma10,
            'bias_ma20': self.bias_ma20,
            'volume_status': self.volume_status.value,
            'volume_ratio_5d': self.volume_ratio_5d,
            'volume_trend': self.volume_trend,
            'support_ma5': self.support_ma5,
            'support_ma10': self.support_ma10,
            'buy_signal': self.buy_signal.value,
            'signal_score': self.signal_score,
            'signal_reasons': self.signal_reasons,
            'risk_factors': self.risk_factors,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'macd_bar': self.macd_bar,
            'macd_status': self.macd_status.value,
            'macd_signal': self.macd_signal,
            'rsi_6': self.rsi_6,
            'rsi_12': self.rsi_12,
            'rsi_24': self.rsi_24,
            'rsi_status': self.rsi_status.value,
            'rsi_signal': self.rsi_signal,
            'is_limit_up': self.is_limit_up,
            'limit_up_reason': self.limit_up_reason,
            'ai_analysis_report': self.ai_analysis_report
        }


class StockTrendAnalyzer:
    VOLUME_SHRINK_RATIO = 0.7
    VOLUME_HEAVY_RATIO = 1.5
    MA_SUPPORT_TOLERANCE = 0.02
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    RSI_SHORT = 6
    RSI_MID = 12
    RSI_LONG = 24
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    
    def __init__(self):
        pass
    
    def analyze(self, df: pd.DataFrame, code: str) -> TrendAnalysisResult:
        result = TrendAnalysisResult(code=code)
        
        if df is None or df.empty or len(df) < 20:
            logger.warning(f"{code} 数据不足，无法进行趋势分析")
            result.risk_factors.append("数据不足，无法完成分析")
            return result
        
        df = df.sort_values('date').reset_index(drop=True)
        df = self._calculate_mas(df)
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)

        latest = df.iloc[-1]
        result.current_price = float(latest['close'])
        result.ma5 = float(latest['MA5'])
        result.ma10 = float(latest['MA10'])
        result.ma20 = float(latest['MA20'])
        result.ma60 = float(latest.get('MA60', 0))

        self._analyze_trend(df, result)
        self._calculate_bias(result)
        self._analyze_volume(df, result)
        self._analyze_support_resistance(df, result)
        self._analyze_macd(df, result)
        self._analyze_rsi(df, result)
        self._generate_signal(result)
        
        # ==========================================
        # 🚀 [第3点核心落地]：接轨分时形态量化
        # ==========================================
        self._analyze_limit_up_stock_premium(result, df)
        
        return result
    
    def _calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['MA5'] = df['close'].rolling(window=5).mean()
        df['MA10'] = df['close'].rolling(window=10).mean()
        df['MA20'] = df['close'].rolling(window=20).mean()
        if len(df) >= 60:
            df['MA60'] = df['close'].rolling(window=60).mean()
        else:
            df['MA60'] = df['MA20']
        return df

    def _calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        ema_fast = df['close'].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.MACD_SLOW, adjust=False).mean()
        df['MACD_DIF'] = ema_fast - ema_slow
        df['MACD_DEA'] = df['MACD_DIF'].ewm(span=self.MACD_SIGNAL, adjust=False).mean()
        df['MACD_BAR'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2
        return df

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for period in [self.RSI_SHORT, self.RSI_MID, self.RSI_LONG]:
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.fillna(50)
            df[f'RSI_{period}'] = rsi
        return df
    
    def _analyze_trend(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20
        if ma5 > ma10 > ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev['MA5'] - prev['MA20']) / prev['MA20'] * 100 if prev['MA20'] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "强势多头排列，均线发散上行"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "多头排列 MA5>MA10>MA20"
                result.trend_strength = 75
        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "弱势多头"
            result.trend_strength = 55
        elif ma5 < ma10 < ma20:
            result.trend_status = TrendStatus.BEAR
            result.ma_alignment = "空头排列"
            result.trend_strength = 25
        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "均线缠绕"
            result.trend_strength = 50
    
    def _calculate_bias(self, result: TrendAnalysisResult) -> None:
        price = result.current_price
        if result.ma5 > 0:
            result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
        if result.ma10 > 0:
            result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
        if result.ma20 > 0:
            result.bias_ma20 = (price - result.ma20) / result.ma20 * 100
    
    def _analyze_volume(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        if len(df) < 5:
            return
        latest = df.iloc[-1]
        vol_5d_avg = df['volume'].iloc[-6:-1].mean()
        if vol_5d_avg > 0:
            result.volume_ratio_5d = float(latest['volume']) / vol_5d_avg
        prev_close = df.iloc[-2]['close']
        price_change = (latest['close'] - prev_close) / prev_close * 100
        
        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            result.volume_status = VolumeStatus.HEAVY_VOLUME_UP if price_change > 0 else VolumeStatus.HEAVY_VOLUME_DOWN
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            result.volume_status = VolumeStatus.SHRINK_VOLUME_UP if price_change > 0 else VolumeStatus.SHRINK_VOLUME_DOWN
        else:
            result.volume_status = VolumeStatus.NORMAL
    
    def _analyze_support_resistance(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        price = result.current_price
        if result.ma5 > 0 and abs(price - result.ma5) / result.ma5 <= self.MA_SUPPORT_TOLERANCE and price >= result.ma5:
            result.support_ma5 = True
        if result.ma10 > 0 and abs(price - result.ma10) / result.ma10 <= self.MA_SUPPORT_TOLERANCE and price >= result.ma10:
            result.support_ma10 = True

    def _analyze_macd(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        if len(df) < self.MACD_SLOW:
            return
        latest = df.iloc[-1]
        result.macd_dif = float(latest['MACD_DIF'])
        result.macd_dea = float(latest['MACD_DEA'])
        result.macd_bar = float(latest['MACD_BAR'])
        if result.macd_dif > 0 and result.macd_dea > 0:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "✓ 多头区间"

    def _analyze_rsi(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        if len(df) < self.RSI_LONG:
            return
        latest = df.iloc[-1]
        result.rsi_12 = float(latest[f'RSI_{self.RSI_MID}'])
        if result.rsi_12 > self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
        else:
            result.rsi_status = RSIStatus.NEUTRAL

    def _generate_signal(self, result: TrendAnalysisResult) -> None:
        # 保留基础评分骨架
        score = 60
        if result.trend_status in [TrendStatus.STRONG_BULL, TrendStatus.BULL]:
            score += 15
        if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            score += 10
        result.signal_score = score
        result.buy_signal = BuySignal.BUY if score >= 60 else BuySignal.WAIT

    # ========================================================================
    # 🚀 [第3点核心改进]：深度对接昨日涨停个股的分时指标，打造高级智能提示词
    # ========================================================================
    def _analyze_limit_up_stock_premium(self, result: TrendAnalysisResult, df: pd.DataFrame):
        from main import GLOBAL_LIMIT_UP_DETAIL_MAP
        
        code = result.code
        # 从 main.py 维护的高级数据结构里抓取特定的分时特征数据
        if code in GLOBAL_LIMIT_UP_DETAIL_MAP:
            info = GLOBAL_LIMIT_UP_DETAIL_MAP[code]
            result.is_limit_up = True
            result.limit_up_reason = info["reason"]
            
            # 基础条件加分
            result.signal_reasons.append(f"🔥 昨日涨停：题材归属【{info['reason']}】，天梯身位【{info['status_text']}】")
            
            # 分时量化扣分加分调节
            first_time_str = info["first_time"]
            open_count = info["open_count"]
            
            # 时间特征量化
            try:
                time_parts = first_time_str.split(":")
                hour, minute = int(time_parts[0]), int(time_parts[1])
                if hour == 9 and minute <= 40:
                    result.signal_reasons.append(f"⚡ 封板极早({first_time_str})：属于超预期强动能板，次日高溢价概率大。")
                    result.signal_score += 15
                elif hour >= 14:
                    result.risk_factors.append(f"⚠️ 尾盘偷袭板({first_time_str})：主力封板坚决度不足，次日谨防低开。")
                    result.signal_score -= 10
            except Exception:
                pass
                
            # 炸板分歧量化
            if open_count == 0:
                result.signal_reasons.append("💎 零炸板死封：筹码结构锁定优异，主力控盘度极高。")
                result.signal_score += 10
            elif open_count >= 3:
                result.risk_factors.append(f"⚠️ 多次炸板：全天炸板开闸 {open_count} 次，说明分歧巨大，筹码散落。")
                result.signal_score -= 15
                
            result.buy_signal = BuySignal.STRONG_BUY if result.signal_score >= 75 else BuySignal.BUY
            
            # 将清洗完的精细化游资分时指标组装成高级 Prompt，供核心管线直接唤醒 Gemini 
            result.ai_analysis_report = self._build_premium_llm_prompt(result, info)
        else:
            # 兜底：处理非涨停池但有环境变量传递的场景
            zt_context = os.getenv("ZT_CONTEXT", "")
            if zt_context:
                result.is_limit_up = True
                result.limit_up_reason = zt_context
                result.ai_analysis_report = f"【昨日涨停驱动】：{zt_context}\n【技术形态】：{result.trend_status.value}"

    def _build_premium_llm_prompt(self, result: TrendAnalysisResult, info: dict) -> str:
        """
        构建蕴含分时含金量的高级核心 Prompt 提示词
        """
        prompt = f"""
        请作为顶级游资席位掌舵人，结合该股最新的【短线天梯阶梯】与【分时封板质量】进行硬核盘前推演：
        
        【个股短线分时档案】:
        - 代码名称: {result.code} | {result.name}
        - 所属核心题材: {info['reason']}
        - 当前连板高度: {info['status_text']}
        - 首次封板时间: {info['first_time']}
        - 全天炸板次数: {info['open_count']} 次
        - 盘末封单资金: {info['font_money']}
        
        【系统量化初评报告】:
        - 技术得分: {result.signal_score} 分 | 均线趋势: {result.trend_status.value}
        - 加分项: {', '.join(result.signal_reasons) if result.signal_reasons else '无'}
        - 减分隐患: {', '.join(result.risk_factors) if result.risk_factors else '无'}
        
        【你的深度复盘任务】:
        1. 【评定封板含金量】：根据首次封板时间和炸板分歧次数，一针见血指出该股属于“强力缩量主力惜售板”、“爆量强行洗盘板”还是“弱势跟风偷袭板”？
        2. 【题材抗震分离度】：站在天梯图的宏观视角，该股作为该热点板块的龙头还是跟风小弟？次日是否具备抵抗大盘炸板的分离度与主动性？
        3. 【次日临盘推演】：根据盘末封单额和分时质量，预期明日竞价高开几个点算超预期？如果开盘被恶意核按钮、或者高开超过 >5% 对应的最佳买卖应对操盘策略是什么？
        """
        return prompt
    # ========================================================================

    def format_analysis(self, result: TrendAnalysisResult) -> str:
        lines = [
            f"====== {result.code} 趋势分析 =====",
            f"📊 趋势判断: {result.trend_status.value}",
            f"🎯 操作建议:【{result.buy_signal.value}】 评分: {result.signal_score}/100",
        ]
        return "\n".join(lines)


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    analyzer = StockTrendAnalyzer()
    return analyzer.analyze(df, code)
