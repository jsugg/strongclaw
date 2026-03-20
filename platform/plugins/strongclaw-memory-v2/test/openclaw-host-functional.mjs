import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

import strongclawMemoryV2Plugin from "../index.js";
import {
  createPluginApiStub,
} from "./helpers/openclaw-plugin-sdk-stub.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const pluginRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(__dirname, "../../../..");

function stripPluginLogs(output) {
  return output
    .split(/\r?\n/)
    .filter((line) => line.trim() && !line.startsWith("[plugins]"))
    .join("\n")
    .trim();
}

function writeMemoryV2Config(workspaceDir, configPath) {
  mkdirSync(workspaceDir, { recursive: true });
  writeFileSync(
    configPath,
    [
      "storage:",
      "  db_path: .openclaw/test-memory-v2.sqlite",
      "workspace:",
      "  root: .",
      "  include_default_memory: true",
      "  memory_file_names:",
      "    - MEMORY.md",
      "    - memory.md",
      "  daily_dir: memory",
      "  bank_dir: bank",
      "corpus:",
      "  paths:",
      '    - name: docs',
      "      path: docs",
      '      pattern: "**/*.md"',
      "limits:",
      "  max_snippet_chars: 240",
      "  default_max_results: 6",
    ].join("\n") + "\n",
    "utf8",
  );

  mkdirSync(path.join(workspaceDir, "docs"), { recursive: true });
  mkdirSync(path.join(workspaceDir, "memory"), { recursive: true });
  mkdirSync(path.join(workspaceDir, "bank"), { recursive: true });
  writeFileSync(
    path.join(workspaceDir, "MEMORY.md"),
    "# Project Memory\n\n- Fact: Alice owns the deployment playbook.\n",
    "utf8",
  );
  writeFileSync(
    path.join(workspaceDir, "docs", "runbook.md"),
    "# Gateway Runbook\n\nRotate the gateway token before enabling a new browser profile.\n",
    "utf8",
  );
}

function runOpenClaw(profile, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn("openclaw", ["--profile", profile, "--no-color", ...args], {
      cwd: repoRoot,
      env: { ...process.env, ...(options.env || {}) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timeoutMs = options.timeoutMs ?? 120_000;
    const timer = setTimeout(() => {
      if (settled) {
        return;
      }
      settled = true;
      child.kill("SIGTERM");
      reject(new Error(`openclaw ${args.join(" ")} timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      const combined = [stdout, stderr].filter(Boolean).join("\n").trim();
      if ((code ?? 1) !== 0) {
        reject(
          new Error(
            `openclaw ${args.join(" ")} failed with code ${code ?? "unknown"}\n${combined}`,
          ),
        );
        return;
      }
      resolve(combined);
    });
  });
}

async function main() {
  const runDir = mkdtempSync(path.join(tmpdir(), "strongclaw-memory-v2-openclaw-host-"));
  const profile = `strongclaw-memv2-${Date.now()}`;
  const profileDir = path.join(os.homedir(), `.openclaw-${profile}`);
  const configFile = path.join(profileDir, "openclaw.json");
  const workspaceDir = path.join(runDir, "workspace");
  const memoryConfigPath = path.join(workspaceDir, "memory-v2.yaml");

  try {
    writeMemoryV2Config(workspaceDir, memoryConfigPath);
    rmSync(profileDir, { recursive: true, force: true });
    mkdirSync(profileDir, { recursive: true });
    writeFileSync(
      configFile,
      JSON.stringify(
        {
          plugins: {
            allow: ["strongclaw-memory-v2"],
            load: {
              paths: [pluginRoot],
            },
            slots: {
              memory: "strongclaw-memory-v2",
            },
            entries: {
              "strongclaw-memory-v2": {
                enabled: true,
                config: {
                  configPath: memoryConfigPath,
                  command: ["uv", "run", "--project", repoRoot, "python", "-m", "clawops"],
                  autoRecall: false,
                  autoReflect: false,
                  timeoutMs: 20_000,
                },
              },
            },
          },
        },
        null,
        2,
      ),
      "utf8",
    );

    const validateOutput = await runOpenClaw(profile, ["config", "validate"]);
    assert.match(validateOutput, /Config valid/i);

    const infoOutput = await runOpenClaw(profile, ["plugins", "info", "strongclaw-memory-v2"]);
    assert.match(infoOutput, /Status:\s+loaded/i);
    assert.match(infoOutput, /CLI commands:\s+memory-v2/i);
    assert.match(infoOutput, /\bmemory\b/i);

    const statusOutput = stripPluginLogs(await runOpenClaw(profile, ["memory-v2", "status", "--json"]));
    assert.match(statusOutput, /backendActive|schemaVersion/);

    const hostSearchOutput = stripPluginLogs(
      await runOpenClaw(profile, ["memory-v2", "search", "--query", "gateway token", "--json"]),
    );
    assert.match(hostSearchOutput, /docs\/runbook\.md/);
    assert.match(hostSearchOutput, /Gateway Runbook|gateway token/i);

    const hostGetOutput = stripPluginLogs(
      await runOpenClaw(profile, ["memory-v2", "get", "docs/runbook.md", "--json"]),
    );
    assert.match(hostGetOutput, /docs\/runbook\.md/);
    assert.match(hostGetOutput, /Gateway Runbook/);

    const stub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: ["uv", "run", "--project", repoRoot, "python", "-m", "clawops"],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 20_000,
    });
    strongclawMemoryV2Plugin.register(stub.api);

    const memorySearch = stub.tools.get("memory_search");
    assert.ok(memorySearch);
    const searchResult = await memorySearch.execute("tool-1", {
      query: "gateway token",
      lane: "all",
    });
    const searchPayload = searchResult.details;
    assert.ok(Array.isArray(searchPayload.results));
    assert.ok(searchPayload.results.length >= 1);
    assert.equal(searchPayload.results[0].path, "docs/runbook.md");

    const memoryGet = stub.tools.get("memory_get");
    assert.ok(memoryGet);
    const getResult = await memoryGet.execute("tool-2", {
      path: "docs/runbook.md",
    });
    const getPayload = getResult.details;
    assert.equal(getPayload.path, "docs/runbook.md");
    assert.match(getPayload.text, /Gateway Runbook/);
    assert.equal(stub.cliHandlers.length, 1);

    console.log("OK: strongclaw-memory-v2 OpenClaw host test passed");
  } finally {
    rmSync(profileDir, { recursive: true, force: true });
    rmSync(runDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
