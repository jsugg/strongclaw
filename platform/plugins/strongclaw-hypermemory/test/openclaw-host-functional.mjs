import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

import strongclawHypermemoryPlugin from "../index.js";
import {
  createPluginApiStub,
} from "./helpers/openclaw-plugin-sdk-stub.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "../../../..");

function writeHypermemoryConfig(workspaceDir, configPath) {
  mkdirSync(workspaceDir, { recursive: true });
  writeFileSync(
    configPath,
    [
      "storage:",
      "  db_path: .openclaw/test-hypermemory.sqlite",
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

function parseCommandName(definition) {
  return String(definition).trim().split(/[ <[]/, 1)[0];
}

function createCommandNode(name) {
  return {
    name,
    description: "",
    options: [],
    subcommands: [],
  };
}

function buildCommandApi(node) {
  return {
    description(text) {
      node.description = text;
      return this;
    },
    option(flag, description, defaultValue) {
      node.options.push({ flag, description, defaultValue });
      return this;
    },
    requiredOption(flag, description, defaultValue) {
      node.options.push({ flag, description, defaultValue, required: true });
      return this;
    },
    action(handler) {
      node.action = handler;
      return this;
    },
    command(definition) {
      const child = createCommandNode(parseCommandName(definition));
      node.subcommands.push(child);
      return buildCommandApi(child);
    },
  };
}

function createProgramStub() {
  const commands = [];
  return {
    commands,
    program: {
      command(definition) {
        const node = createCommandNode(parseCommandName(definition));
        commands.push(node);
        return buildCommandApi(node);
      },
    },
  };
}

async function main() {
  const runDir = mkdtempSync(path.join(tmpdir(), "strongclaw-hypermemory-openclaw-host-"));
  const workspaceDir = path.join(runDir, "workspace");
  const memoryConfigPath = path.join(workspaceDir, "hypermemory.sqlite.yaml");

  try {
    writeHypermemoryConfig(workspaceDir, memoryConfigPath);

    const stub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: ["uv", "run", "--project", repoRoot, "python", "-m", "clawops"],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 20_000,
    });
    strongclawHypermemoryPlugin.register(stub.api);

    assert.equal(stub.cliHandlers.length, 1);
    assert.equal(stub.cliRegistrations.length, 1);
    assert.deepEqual(stub.cliRegistrations[0].options.commands, ["memory"]);

    const { program, commands } = createProgramStub();
    stub.cliRegistrations[0].handler({ program });
    assert.deepEqual(commands.map((command) => command.name), ["memory"]);
    assert.deepEqual(
      commands[0].subcommands.map((command) => command.name),
      ["status", "index", "search", "get", "store", "update", "reflect"],
    );

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

    console.log("OK: strongclaw-hypermemory host contract test passed");
  } finally {
    rmSync(runDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exitCode = 1;
});
