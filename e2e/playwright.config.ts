import { defineConfig } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const repo = path.resolve(__dirname, "..");
const python =
  process.platform === "win32"
    ? path.join(repo, ".venv", "Scripts", "python.exe")
    : path.join(repo, ".venv", "bin", "python");
const port = Number(process.env.PYROTV_E2E_PORT ?? 8790);

// Fresh temp data dir with 45 s sample media (the +10 s seek must not end the
// film). Created once in the main process; workers inherit the env var.
// Removed again by cleanup-reporter.ts.
if (!process.env.PYROTV_E2E_DATA || !existsSync(process.env.PYROTV_E2E_DATA)) {
  const dir = mkdtempSync(path.join(tmpdir(), "pyrotv-e2e-"));
  execFileSync(
    python,
    [
      path.join(repo, "backend", "tools", "make_sample_media.py"),
      path.join(dir, "media"),
      "--catalogue",
      path.join(dir, "catalogue.json"), // keep the repo catalogue untouched
      "--duration",
      "45",
    ],
    { stdio: "ignore" },
  );
  process.env.PYROTV_E2E_DATA = dir;
}

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  reporter: [["list"], ["./cleanup-reporter.ts"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1280, height: 800 },
    channel: "chrome",
    launchOptions: { args: ["--autoplay-policy=no-user-gesture-required"] },
    trace: "retain-on-failure",
  },
  projects: [{ name: "desktop" }],
  webServer: {
    command: `"${python}" "${path.join(__dirname, "run_server.py")}"`,
    cwd: path.join(repo, "backend"),
    url: `http://127.0.0.1:${port}/api/state`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      PYROTV_FAKE_HW: "1",
      PYROTV_DATA_DIR: process.env.PYROTV_E2E_DATA!,
      PYROTV_E2E_PORT: String(port),
    },
  },
});
