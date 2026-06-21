"""HF Dataset acceptance test (runs when the fixed dependency is installed)."""

from __future__ import annotations

import pytest

pytest.importorskip("datasets")

from data.build_dataset import REQUIRED_COLUMNS, build  # noqa: E402


def test_build_returns_finite_dataset_with_required_columns() -> None:
    train_dataset = build("train")
    eval_dataset = build("eval")
    assert len(train_dataset) == 8
    assert len(eval_dataset) > 0
    assert REQUIRED_COLUMNS.issubset(train_dataset.column_names)
