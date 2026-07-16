"""LightGBM版 方向モデル。DirectionModelと同一契約(fit/predict)で差し替え可能。

軽量ロジスティックは方向的中~51%で頭打ち。勾配ブースティングで、セッション×
モメンタム等の非線形交互作用を捉えられるか検証する。ラベルはトリプルバリア
(その足でLONG建て→+take_pips到達が-stop_pips到達より先か)で DirectionModel と同一。

学習窓が小さい(240)と過学習するので、保守的パラメータ(浅い木・強い正則化・
min_child_samples)で使う。学習サンプル不足時はモメンタムにフォールバック。
"""
from __future__ import annotations

import math

import numpy as np

try:
    import lightgbm as lgb
except ImportError:  # フォールバック: lightgbm未導入なら常にモメンタム
    lgb = None

from .indicators import feature_vector
from .models import Candle
from .predictor import FEATURES


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class GBMDirectionModel:
    """LightGBM二値分類。P(LONGが+TPを-SLより先に達成)を出す。"""

    def __init__(self) -> None:
        self.booster = None
        self.samples = 0

    def fit(
        self,
        candles: list[Candle],
        epochs: int = 0,  # 契約互換のため受けるが未使用
        learning_rate: float = 0.05,
        *,
        pip_size: float = 0.01,
        stop_pips: float = 25.0,
        take_pips: float = 40.0,
        max_hold: int = 32,
    ) -> None:
        rows: list[list[float]] = []
        labels: list[float] = []
        up = take_pips * pip_size
        dn = stop_pips * pip_size
        for end in range(30, len(candles) - max_hold):
            values = feature_vector(candles, end + 1)
            entry = candles[end].close
            tp = entry + up
            sl = entry - dn
            label = None
            for j in range(end + 1, end + max_hold + 1):
                c = candles[j]
                if c.low <= sl:
                    label = 0.0
                    break
                if c.high >= tp:
                    label = 1.0
                    break
            if label is None:
                label = 1.0 if candles[end + max_hold].close > entry else 0.0
            rows.append([values[name] for name in FEATURES])
            labels.append(label)

        self.samples = len(rows)
        if lgb is None or self.samples < 80:
            self.booster = None
            return
        y = np.asarray(labels)
        if y.min() == y.max():  # 片側クラスしかない
            self.booster = None
            return
        x = np.asarray(rows)
        dataset = lgb.Dataset(x, label=y)
        params = {
            "objective": "binary",
            "learning_rate": learning_rate,
            "num_leaves": 15,
            "max_depth": 4,
            "min_child_samples": 20,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l1": 0.5,
            "lambda_l2": 0.5,
            "verbose": -1,
        }
        self.booster = lgb.train(params, dataset, num_boost_round=120)

    def predict(self, candles: list[Candle]) -> tuple[float, dict[str, float]]:
        features = feature_vector(candles)
        if self.booster is None:
            momentum = 55 * features["ma_gap"] + 35 * features["return_6"]
            return _sigmoid(momentum), features
        row = np.asarray([[features[name] for name in FEATURES]])
        prob = float(self.booster.predict(row)[0])
        return prob, features
