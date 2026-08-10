import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const workingDirectory = resolve(process.cwd());
const repositoryRoot = dirname(workingDirectory);
const isSourceFrontend =
  existsSync(join(repositoryRoot, ".git")) &&
  existsSync(join(repositoryRoot, "scripts", "run_frontend.py"));

if (isSourceFrontend) {
  console.error(
    "Refusing to run npm in the source frontend. Use: python scripts/run_frontend.py <command>",
  );
  process.exit(1);
}

console.log(`Frontend runtime guard passed: ${workingDirectory}`);
