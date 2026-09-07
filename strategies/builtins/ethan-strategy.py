import pandas as pd
import numpy as np


# ============================================================
# Linear Regression
# ============================================================

def linear_regression(series, length=14):
    """
    Rolling Linear Regression value tại bar hiện tại.
    """
    x = np.arange(length, dtype=float)

    def calc(y):
        if np.isnan(y).any():
            return np.nan

        slope, intercept = np.polyfit(x, y, 1)
        return intercept + slope * (length - 1)

    return series.rolling(length).apply(calc, raw=True)


# ============================================================
# SuperTrend
# ============================================================

def supertrend(df, period=10, multiplier=3.0):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    hl2 = (high + low) / 2

    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()

    for i in range(1, len(df)):
        if (
            upper_basic.iloc[i] < upper_band.iloc[i - 1]
            or close.iloc[i - 1] > upper_band.iloc[i - 1]
        ):
            upper_band.iloc[i] = upper_basic.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        if (
            lower_basic.iloc[i] > lower_band.iloc[i - 1]
            or close.iloc[i - 1] < lower_band.iloc[i - 1]
        ):
            lower_band.iloc[i] = lower_basic.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = np.nan
            direction.iloc[i] = 1
            continue

        if st.iloc[i - 1] == upper_band.iloc[i - 1]:
            if close.iloc[i] <= upper_band.iloc[i]:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
        else:
            if close.iloc[i] >= lower_band.iloc[i]:
                st.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1

    return st


# ============================================================
# Strategy
# ============================================================

class LongShortStrategy:

    def __init__(
        self,
        pip_size=0.0001,

        # Long
        long_lookback=610,
        long_valid_bars=9,
        long_sl_pips=805,
        long_tp_pips=2350,

        # Short
        short_lookback=615,
        short_valid_bars=4,
        short_sl_pips=865,
        short_tp_pips=2255,
        short_max_bars=36,

        # SuperTrend
        st_period=10,
        st_multiplier=3.0,

        initial_capital=10000
    ):
        self.pip_size = pip_size

        self.long_lookback = long_lookback
        self.long_valid_bars = long_valid_bars
        self.long_sl_pips = long_sl_pips
        self.long_tp_pips = long_tp_pips

        self.short_lookback = short_lookback
        self.short_valid_bars = short_valid_bars
        self.short_sl_pips = short_sl_pips
        self.short_tp_pips = short_tp_pips
        self.short_max_bars = short_max_bars

        self.st_period = st_period
        self.st_multiplier = st_multiplier

        self.initial_capital = initial_capital

    # --------------------------------------------------------
    # Prepare indicators
    # --------------------------------------------------------

    def prepare(self, df, daily_df):

        df = df.copy()
        daily_df = daily_df.copy()

        # 14-period Linear Regression
        df["linreg14"] = linear_regression(
            df["close"],
            14
        )

        # SuperTrend
        df["supertrend"] = supertrend(
            df,
            self.st_period,
            self.st_multiplier
        )

        # ----------------------------------------------------
        # Align Daily OHLC to main timeframe
        # ----------------------------------------------------

        daily = daily_df[["open", "high", "low", "close"]].copy()

        daily = daily.rename(columns={
            "open": "daily_open",
            "high": "daily_high",
            "low": "daily_low",
            "close": "daily_close"
        })

        # Daily values available to each main-chart bar
        df = pd.merge_asof(
            df.sort_index(),
            daily.sort_index(),
            left_index=True,
            right_index=True,
            direction="backward"
        )

        return df

    # --------------------------------------------------------
    # Long condition
    # --------------------------------------------------------

    def long_signal(self, df, i):

        if i < 3:
            return False

        # "High price from two bars ago is higher than
        # the low of the daily chart for three consecutive bars."
        #
        # Interpreted as:
        # high[i-2] > daily_low[i]
        # high[i-3] > daily_low[i-1]
        # high[i-4] > daily_low[i-2]

        if i < 4:
            return False

        cond1 = (
            df["high"].iloc[i - 2]
            > df["daily_low"].iloc[i]
        )

        cond2 = (
            df["high"].iloc[i - 3]
            > df["daily_low"].iloc[i - 1]
        )

        cond3 = (
            df["high"].iloc[i - 4]
            > df["daily_low"].iloc[i - 2]
        )

        daily_low_condition = cond1 and cond2 and cond3

        # Opening price from three bars ago
        # below 14-period Linear Regression
        cond4 = (
            df["open"].iloc[i - 3]
            < df["linreg14"].iloc[i - 3]
        )

        return daily_low_condition and cond4

    # --------------------------------------------------------
    # Short condition
    # --------------------------------------------------------

    def short_signal(self, df, i):

        if i < 8:
            return False

        # Open price from two bars ago on daily chart
        # > high of main chart for six consecutive bars
        daily_open_condition = True

        for k in range(6):
            idx = i - k

            if not (
                df["daily_open"].iloc[idx - 2]
                > df["high"].iloc[idx]
            ):
                daily_open_condition = False
                break

        # SuperTrend > open price from three bars ago
        # for six consecutive bars
        supertrend_condition = True

        for k in range(6):
            idx = i - k

            if not (
                df["supertrend"].iloc[idx]
                > df["open"].iloc[idx - 3]
            ):
                supertrend_condition = False
                break

        return (
            daily_open_condition
            and supertrend_condition
        )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    def backtest(self, df, daily_df):

        df = self.prepare(df, daily_df)

        trades = []

        position = None
        pending = None

        for i in range(len(df)):

            row = df.iloc[i]
            timestamp = df.index[i]

            # =================================================
            # 1. Check existing position
            # =================================================

            if position is not None:

                entry = position["entry"]
                side = position["side"]

                bars_open = i - position["entry_bar"]

                if side == "long":

                    sl = position["sl"]
                    tp = position["tp"]

                    # Conservative assumption:
                    # SL checked before TP if both touched
                    if row["low"] <= sl:

                        exit_price = sl
                        reason = "stop_loss"

                    elif row["high"] >= tp:

                        exit_price = tp
                        reason = "take_profit"

                    else:
                        exit_price = None

                    if exit_price is not None:

                        pnl = exit_price - entry

                        trades.append({
                            "entry_time": position["entry_time"],
                            "exit_time": timestamp,
                            "side": "LONG",
                            "entry": entry,
                            "exit": exit_price,
                            "sl": sl,
                            "tp": tp,
                            "pnl_price": pnl,
                            "pnl_pips":
                                pnl / self.pip_size,
                            "bars": bars_open,
                            "reason": reason
                        })

                        position = None

                elif side == "short":

                    sl = position["sl"]
                    tp = position["tp"]

                    if row["high"] >= sl:

                        exit_price = sl
                        reason = "stop_loss"

                    elif row["low"] <= tp:

                        exit_price = tp
                        reason = "take_profit"

                    elif bars_open >= self.short_max_bars:

                        exit_price = row["close"]
                        reason = "time_exit"

                    else:
                        exit_price = None

                    if exit_price is not None:

                        pnl = entry - exit_price

                        trades.append({
                            "entry_time": position["entry_time"],
                            "exit_time": timestamp,
                            "side": "SHORT",
                            "entry": entry,
                            "exit": exit_price,
                            "sl": sl,
                            "tp": tp,
                            "pnl_price": pnl,
                            "pnl_pips":
                                pnl / self.pip_size,
                            "bars": bars_open,
                            "reason": reason
                        })

                        position = None

            # =================================================
            # 2. Check pending order
            # =================================================

            if pending is not None and position is None:

                pending["bars_waiting"] += 1

                # Expire pending order
                if (
                    pending["bars_waiting"]
                    > pending["valid_bars"]
                ):
                    pending = None

                else:

                    # -----------------------------------------
                    # LONG pending buy at rolling highest high
                    # -----------------------------------------

                    if pending["side"] == "long":

                        entry_price = pending["entry"]

                        if row["high"] >= entry_price:

                            sl = (
                                entry_price
                                - self.long_sl_pips
                                * self.pip_size
                            )

                            tp = (
                                entry_price
                                + self.long_tp_pips
                                * self.pip_size
                            )

                            position = {
                                "side": "long",
                                "entry": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "entry_bar": i,
                                "entry_time": timestamp
                            }

                            pending = None

                    # -----------------------------------------
                    # SHORT pending sell at rolling lowest low
                    # -----------------------------------------

                    elif pending["side"] == "short":

                        entry_price = pending["entry"]

                        if row["low"] <= entry_price:

                            sl = (
                                entry_price
                                + self.short_sl_pips
                                * self.pip_size
                            )

                            tp = (
                                entry_price
                                - self.short_tp_pips
                                * self.pip_size
                            )

                            position = {
                                "side": "short",
                                "entry": entry_price,
                                "sl": sl,
                                "tp": tp,
                                "entry_bar": i,
                                "entry_time": timestamp
                            }

                            pending = None

            # =================================================
            # 3. Generate new signals
            # =================================================

            if position is None and pending is None:

                # ---------------------------------------------
                # LONG
                # ---------------------------------------------

                if self.long_signal(df, i):

                    if i >= self.long_lookback:

                        highest_price = (
                            df["high"]
                            .iloc[
                                i - self.long_lookback + 1:
                                i + 1
                            ]
                            .max()
                        )

                        pending = {
                            "side": "long",
                            "entry": highest_price,
                            "valid_bars":
                                self.long_valid_bars,
                            "bars_waiting": 0,
                            "signal_bar": i
                        }

                # ---------------------------------------------
                # SHORT
                # ---------------------------------------------

                elif self.short_signal(df, i):

                    if i >= self.short_lookback:

                        lowest_price = (
                            df["low"]
                            .iloc[
                                i - self.short_lookback + 1:
                                i + 1
                            ]
                            .min()
                        )

                        pending = {
                            "side": "short",
                            "entry": lowest_price,
                            "valid_bars":
                                self.short_valid_bars,
                            "bars_waiting": 0,
                            "signal_bar": i
                        }

        return pd.DataFrame(trades)
