"""Exact gather-only strategy selection; one launch per chronological clock."""
import triton
import triton.language as tl


@triton.jit
def select(RAW, BRANCH, VALID, POSITION, OWNER, RATIOS, POLICY, AGES, COLUMNS,
           OUT, OUT_RATIOS, OUT_POLICY, OUT_AGES, OUT_COLUMNS,
           B: tl.constexpr, U: tl.constexpr, W: tl.constexpr, BLOCK: tl.constexpr):
    x = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
    mask = x < B * U
    candidate, ticker = x // U, x % U
    proposed = tl.full((BLOCK,), 0, tl.int64)
    found = tl.full((BLOCK,), False, tl.int1)
    for branch in range(W):
        leaf = tl.load(BRANCH + candidate * W + branch, mask, 0)
        valid = tl.load(VALID + candidate * W + branch, mask, False)
        signal = tl.load(RAW + leaf * U + ticker, mask, 0)
        take = valid & ((signal & 1) != 0) & ~found
        proposed = tl.where(take, branch, proposed)
        found = found | take
    held = tl.load(POSITION + x * 9, mask, 0) > 1.e-12
    owner = tl.where(held, tl.load(OWNER + x, mask, 0), proposed)
    tl.store(OWNER + x, owner, mask)
    leaf = tl.load(BRANCH + candidate * W + owner, mask, 0)
    tl.store(OUT + x, tl.load(RAW + leaf * U + ticker, mask, 0), mask)
    for k in tl.static_range(3):
        tl.store(OUT_RATIOS + x * 3 + k, tl.load(RATIOS + (leaf * U + ticker) * 3 + k, mask, 0), mask)
    for k in tl.static_range(9):
        tl.store(OUT_POLICY + x * 9 + k, tl.load(POLICY + leaf * 9 + k, mask, 0), mask)
    for k in tl.static_range(5):
        tl.store(OUT_AGES + x * 5 + k, tl.load(AGES + leaf * 5 + k, mask, 0), mask)
    tl.store(OUT_COLUMNS + x, tl.load(COLUMNS + leaf, mask, 0), mask)


def route(plan, raw, position, out):
    b, u = plan.workspace_shape
    select[(triton.cdiv(b * u, 128),)](
        raw, plan.branch_index, plan.branch_valid, position, plan.owner,
        plan.base.ratios, plan.policies, plan.ages, plan.leaf_columns,
        out, plan.ratios, plan.selected_policy, plan.selected_limits, plan.selected_columns,
        b, u, plan.branch_index.shape[1], 128)
    return out
