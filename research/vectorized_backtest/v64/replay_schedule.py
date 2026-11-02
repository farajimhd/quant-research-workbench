"""Proven journal bounds, independent of strategy decisions and replay outcomes."""


def chunk_clocks(block, listings, order_capacity, episode_capacity, *, tail=False):
    if min(block, listings, order_capacity, episode_capacity) < 1:
        raise ValueError('Positive replay and journal dimensions required')
    # Each decision clock can cancel one order and complete one replacement.
    # One position per listing can close. Pages are drained at each boundary.
    if order_capacity < 2 * block * listings or episode_capacity < block * listings:
        raise ValueError('Journal cannot hold one captured graph')
    if tail:
        # No entries/additions/reductions are submitted in the terminal tail.
        # A pending order can finish/cancel, followed by one final liquidation;
        # reserve three records per listing conservatively, independent of time.
        if order_capacity < 3 * listings or episode_capacity < listings:
            raise ValueError('Journal cannot hold terminal liquidation')
        return max(block, (512 // block) * block)
    maximum=min(512,order_capacity//(2*listings),episode_capacity//listings)
    return max(block,(maximum//block)*block)
