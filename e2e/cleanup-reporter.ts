import type { Reporter } from "@playwright/test/reporter";
import { rmSync } from "node:fs";

// Removes the temp data dir. Runs in onExit, i.e. after the webServer has been
// stopped (on Windows the running backend still holds pyrotv.db open before that).
export default class CleanupReporter implements Reporter {
  async onExit() {
    const dir = process.env.PYROTV_E2E_DATA;
    if (dir) rmSync(dir, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });
  }
}
