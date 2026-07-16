from __future__ import annotations

import math

from .indicators import feature_vector
from .models import Candle


FEATURES = (
    "return_1", "return_3", "return_6", "return_12", "ma_gap",
    "volatility", "range_mean", "rsi_scaled", "volume_ratio",
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

    def fit(self, candles: list[Candle], epochs: int = 160, learning_rate: float = 0.08) -> None:
        samples: list[list[float]] = []
        labels: list[float] = []
        for end in range(30, len(candles) - 1):
            values = feature_vector(candles, end)
            samples.append([values[name] for name in FEATURES])
            labels.append(1.0 if candles[end].close > candles[end - 1].close else 0.0)
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

