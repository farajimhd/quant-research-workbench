import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const python = process.env.QW_FRONTEND_PYTHON;
if (!python) {
  console.error("QW_FRONTEND_PYTHON is required; run UI review through scripts/run_frontend.py.");
  process.exit(2);
}

const reviewScript = fileURLToPath(new URL("./ui_review.py", import.meta.url));
const result = spawnSync(python, [reviewScript, ...process.argv.slice(2)], {
  env: process.env,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
