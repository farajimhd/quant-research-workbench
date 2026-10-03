import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const python = process.env.QW_FRONTEND_PYTHON;
if (!python) {
  console.error("QW_FRONTEND_PYTHON is required; run UI review through scripts/run_frontend.py.");
  process.exit(2);
}

const researchReview = process.argv.includes("--research-teacher");
const priceActionReview = process.argv.includes("--research-price-action");
const savedLabelsReview = process.argv.includes("--research-saved-labels");
const reviewScript = fileURLToPath(new URL(savedLabelsReview ? "./research_saved_labels_review.py" : priceActionReview ? "./research_price_action_review.py" : researchReview ? "./research_teacher_review.py" : "./ui_review.py", import.meta.url));
const result = spawnSync(python, [reviewScript, ...process.argv.slice(2).filter(arg => !["--research-teacher", "--research-price-action", "--research-saved-labels"].includes(arg))], {
  env: process.env,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
