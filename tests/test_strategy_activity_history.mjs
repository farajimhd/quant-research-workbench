import assert from "node:assert/strict";
import test from "node:test";
import { activitySummaryRow, loadActivityHistory } from "../frontend/src/app/strategyActivityHistory.ts";

test("loads beyond 2000 and follows empty nonterminal pages until explicit completion", async () => {
  const offsets = [];
  const accepted = [];
  let inFlight = 0;
  await loadActivityHistory({ complete: false, next_offset: 2000 }, async (offset) => {
    assert.equal(++inFlight, 1);
    offsets.push(offset);
    await Promise.resolve();
    inFlight--;
    return offset === 2000
      ? { rows: [], complete: false, next_offset: 4000 }
      : { rows: [{ record_id: "oldest" }], complete: true, next_offset: null };
  }, (page) => accepted.push(...page.rows), new AbortController().signal);
  assert.deepEqual(offsets, [2000, 4000]);
  assert.deepEqual(accepted, [{ record_id: "oldest" }]);
});

test("stale results are rejected after a run or clock change cancels the request", async () => {
  const controller = new AbortController();
  const accepted = [];
  await loadActivityHistory({ complete: false, next_offset: 2000 }, async () => {
    controller.abort();
    return { rows: [{ record_id: "wrong-run" }], complete: true };
  }, (page) => accepted.push(...page.rows), controller.signal);
  assert.deepEqual(accepted, []);
});

test("repeated cursors fail explicitly instead of looping or silently claiming completion", async () => {
  let calls = 0;
  await assert.rejects(loadActivityHistory({ complete: false, next_offset: 2000 }, async () => {
    calls++;
    return { rows: [], complete: false, next_offset: 2000 };
  }, () => {}, new AbortController().signal), /did not advance/);
  assert.equal(calls, 1);
});

test("request failure stays observable; completed seeds make no extra request", async () => {
  await assert.rejects(loadActivityHistory({ complete: false, next_offset: 2000 }, async () => {
    throw new Error("offline");
  }, () => {}, new AbortController().signal), /offline/);
  await loadActivityHistory({ complete: true }, async () => assert.fail("unexpected request"), () => {}, new AbortController().signal);
});

test("summary preserves table fields and record identity without retaining nested evidence", () => {
  const row = { record_id: "event-1", reason: "wait", gates: "entry:fail", reference_price: 8.5,
    gate_snapshot: { large: [] }, event_evidence: { large: [] }, management_event: {}, decision_evidence: "full JSON" };
  assert.deepEqual(activitySummaryRow(row), { record_id: "event-1", reason: "wait", gates: "entry:fail", reference_price: 8.5 });
  assert.ok(row.gate_snapshot);
});
