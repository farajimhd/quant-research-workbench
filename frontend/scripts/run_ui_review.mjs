import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const python = process.env.QW_FRONTEND_PYTHON;
if (!python) {
  console.error("QW_FRONTEND_PYTHON is required; run UI review through scripts/run_frontend.py.");
  process.exit(2);
}

const shardsReview = process.argv.includes("--research-shards");
const researchReview = process.argv.includes("--research-teacher");
const v66Review = process.argv.includes("--research-v66-labels");
const priceActionReview = process.argv.includes("--research-price-action");
const savedLabelsReview = process.argv.includes("--research-saved-labels");
const marketReview = process.argv.includes("--research-market-preview");
const programmeReview = process.argv.includes("--programme-backtest-offline");
const reviewScript = fileURLToPath(new URL(shardsReview ? "./research_shards_review.py" : v66Review ? "./research_v66_labels_review.py" : programmeReview ? "./programme_backtest_review.py" : marketReview ? "./research_market_preview_review.py" : savedLabelsReview ? "./research_saved_labels_review.py" : priceActionReview ? "./research_price_action_review.py" : researchReview ? "./research_teacher_review.py" : "./ui_review.py", import.meta.url));
const result = spawnSync(python, [reviewScript, ...process.argv.slice(2).filter(arg => !["--research-shards", "--research-v66-labels", "--research-teacher", "--research-price-action", "--research-saved-labels", "--research-market-preview", "--programme-backtest-offline"].includes(arg))], {
  env: process.env,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
