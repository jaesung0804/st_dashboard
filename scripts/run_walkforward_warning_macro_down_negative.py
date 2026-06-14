from __future__ import annotations

import pandas as pd

import run_walkforward_warning as base
import run_walkforward_warning_macro as macro_runner


ORIGINAL_ADD_TARGETS = base.add_targets


def add_targets_down_negative(frame: pd.DataFrame) -> pd.DataFrame:
    frame = ORIGINAL_ADD_TARGETS(frame)
    frame["down_label_6m_bottom5"] = (frame["future_return_126d"] < 0).astype(int)
    return frame


def main() -> None:
    base.add_targets = add_targets_down_negative
    macro_runner.main()


if __name__ == "__main__":
    main()
