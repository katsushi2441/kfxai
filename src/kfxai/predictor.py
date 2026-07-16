from __future__ import annotations

import math

from .indicators import feature_vector
from .models import Candle


FEATURES = (
    "return_1", "return_3", "return_6", "return_12", "return_24", "return_96",
    "ma_gap", "volatility", "range_mean", "range_pos", "rsi_scaled",
    "volume_ratio", "hour_sin", "hour_cos",
)


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


class DirectionModel:
    """Small deterministic logistic model trained per instrument each cycle.

    This avoids a heavyweight runtime while keeping prediction separate from
    the LLM judgment layer. It can later be replaced by LightGBM without
    changing the strategy contract.
    """

    def __init__(self) -> None:
        self.weights = [0.0] * (len(FEATURES) + 1)
        self.means = [0.0] * len(FEATURES)
        self.scales = [1.0] * len(FEATURES)
        self.samples = 0

    def fit(
        self,
        candles: list[Candle],
        epochs: int = 160,
        learning_rate: float = 0.08,
        *,
        pip_size: float = 0.01,
        stop_pips: float = 25.0,
        take_pips: float = 40.0,
        max_hold: int = 32,
    ) -> None:
        """トリプルバリア・ラベルで学習する。

        ラベル = 「その足の終値でLONGを建てたら、+take_pips到達を-stop_pips到達より
        先に達成するか(=実際に約定する事象)」。max_hold本以内にどちらも触れなければ
        max_hold終値での損益符号でラベル付け(paper決済の max_hold と一致)。
        SL/TP同一足内はSL優先(保守的、simulate_exitと一致)。look-ahead防止済み。
        """
        samples: list[list[float]] = []
        labels: list[float] = []
        up = take_pips * pip_size
        dn = stop_pips * pip_size
        for end in range(30, len(candles) - max_hold):
            # 特徴量はentryバー(end)まで含めて算出=predict(candles[:i+1])と一致させる
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
            if label is None:  # タイムアウト = max_hold終値で決済
                label = 1.0 if candles[end + max_hold].close > entry else 0.0
            samples.append([values[name] for name in FEATURES])
            labels.append(label)
        self.samples = len(samples)
        if self.samples < 40:
            return
        for column in range(len(FEATURES)):
            values = [row[column] for row in samples]
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            self.means[column] = mean
            self.scales[column] = math.sqrt(variance) or 1.0
        normalized = [
            [(value - self.means[i]) / self.scales[i] for i, value in enumerate(row)]
            for row in samples
        ]
        for _ in range(epochs):
            gradients = [0.0] * len(self.weights)
            for row, label in zip(normalized, labels):
                probability = _sigmoid(self.weights[0] + sum(
                    weight * value for weight, value in zip(self.weights[1:], row)
                ))
                error = probability - label
                gradients[0] += error
                for i, value in enumerate(row, start=1):
                    gradients[i] += error * value
            for i in range(len(self.weights)):
                regularization = 0.002 * self.weights[i] if i else 0.0
                self.weights[i] -= learning_rate * (gradients[i] / self.samples + regularization)

    def predict(self, candles: list[Candle]) -> tuple[float, dict[str, float]]:
        features = feature_vector(candles)
        if self.samples < 40:
            momentum = 55 * features["ma_gap"] + 35 * features["return_6"]
            return _sigmoid(momentum), features
        row = [
            (features[name] - self.means[i]) / self.scales[i]
            for i, name in enumerate(FEATURES)
        ]
        score = self.weights[0] + sum(
            weight * value for weight, value in zip(self.weights[1:], row)
        )
        return _sigmoid(score), features

