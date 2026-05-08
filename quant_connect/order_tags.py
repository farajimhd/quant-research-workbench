class OrderTagMixin:

    def entry_tag(
        self,
        reason,
        rank,
        live_rank,
        quantity,
        entry,
        stop,
        score,
        score_quality,
        risk_pct,
        state,
        scanner_top,
        scanner_count,
    ):
        return (
            f"ENTRY|type=STOP|reason={reason}|rule=5M_BOX_LIVE_MACD_TEMA|rank={rank}|lrank={live_rank}"
            f"|qty={quantity}|px={self.value_tag(state.last_price)}|trigger={entry:.2f}|stop={stop:.2f}|box_high={state.orb_high:.2f}"
            f"|box_mid={self.box_mid(state):.2f}|box_low={state.orb_low:.2f}"
            f"|setup={state.orb_score:.1f}|live={score:.1f}|rv={state.orb_relative_volume:.1f}"
            f"|sq={score_quality:.2f}|rp={risk_pct * 100:.2f}"
            f"|ra={self.range_atr(state):.2f}|cl={self.close_location(state):.2f}|br={self.body_to_range(state):.2f}"
            f"|macd={state.macd_line:.4f}|sig={state.macd_signal:.4f}|hist={state.macd_hist:.4f}"
            f"|tema9={state.tema9:.4f}|tema20={state.tema20:.4f}"
            f"|tbuf={self.tema_entry_buffer(state):.4f}"
            f"|scan={scanner_count}|top5={scanner_top}"
        )

    def exit_tag(self, reason, state, replacement_symbol=None, replacement_score=None):
        rotation = ""

        if replacement_symbol is not None:
            rotation = f"|rotate_to={replacement_symbol.Value}|new_score={replacement_score:.1f}"

        return (
            f"EXIT|reason={reason}|price={self.value_tag(state.last_price)}"
            f"|stop={self.protective_stop_price(state):.2f}|box_mid={self.box_mid(state):.2f}|box_high={state.orb_high:.2f}"
            f"|live={self.value_tag(state.orb_live_score)}"
            f"|maxp={self.value_tag(state.max_price_since_entry)}"
            f"|maxu={self.value_tag(state.max_unrealized_profit)}"
            f"|maxR={self.value_tag(state.max_r_multiple)}|gb={self.giveback_tag(state)}"
            f"|held={self.held_seconds_tag(state)}"
            f"|macd={self.value_tag(state.macd_line)}|sig={self.value_tag(state.macd_signal)}"
            f"|hist={self.value_tag(state.macd_hist)}"
            f"|tema9={self.value_tag(state.tema9)}|tema20={self.value_tag(state.tema20)}"
            f"|buf={self.tema_exit_buffer(state):.4f}{rotation}"
        )

    def value_tag(self, value):
        if value is None:
            return "na"

        return f"{value:.4f}"

    def giveback_tag(self, state):
        if (
            state.last_price is None
            or state.orb_entry_price is None
            or state.max_price_since_entry is None
        ):
            return "na"

        max_profit = state.max_price_since_entry - state.orb_entry_price

        if max_profit <= 0:
            return "0.0000"

        giveback = (state.max_price_since_entry - state.last_price) / max_profit
        return f"{max(0.0, giveback):.4f}"

    def held_seconds_tag(self, state):
        if state.orb_entry_time is None:
            return "na"

        return f"{(self.algorithm.Time - state.orb_entry_time).total_seconds():.0f}"
