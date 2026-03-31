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

    assert.throws(() => {
      const missingConfigStub = createPluginApiStub({
        configPath: path.join(workspaceDir, "missing-config.yaml"),
      });
      strongclawHypermemoryPlugin.register(missingConfigStub.api);
    }, /configPath does not exist/);

    const badCommandStub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: ["command-does-not-exist-strongclaw-hypermemory"],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 5_000,
      startupTimeoutMs: 5_000,
      toolTimeoutMs: 5_000,
    });
    strongclawHypermemoryPlugin.register(badCommandStub.api);
    const badSearchTool = badCommandStub.tools.get("memory_search");
    assert.ok(badSearchTool);
    const badSearchResult = await badSearchTool.execute("bad-tool", {
      query: "anything",
    });
    assert.equal(badSearchResult.details.disabled, true);
    assert.match(String(badSearchResult.details.error), /failed to start/);

    const timeoutCommandPath = path.join(runDir, "slow-command.mjs");
    writeFileSync(
      timeoutCommandPath,
      [
        "#!/usr/bin/env node",
        "setTimeout(() => {",
        "  process.stdout.write(\"{}\\n\");",
        "  process.exit(0);",
        "}, 1500);",
      ].join("\n"),
      { encoding: "utf8", mode: 0o755 },
    );
    const timeoutStub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: [timeoutCommandPath],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 20_000,
      startupTimeoutMs: 1_000,
      toolTimeoutMs: 1_000,
    });
    strongclawHypermemoryPlugin.register(timeoutStub.api);
    const timeoutSearchTool = timeoutStub.tools.get("memory_search");
    assert.ok(timeoutSearchTool);
    const timeoutSearchResult = await timeoutSearchTool.execute("timeout-tool", {
      query: "anything",
    });
    assert.equal(timeoutSearchResult.details.disabled, true);
    assert.match(String(timeoutSearchResult.details.error), /startup preflight timed out/i);

    assert.ok("shortTimeoutMs" in strongclawHypermemoryPlugin.configSchema.properties);
    assert.ok("longTimeoutMs" in strongclawHypermemoryPlugin.configSchema.properties);
    const timeoutClassCommandPath = path.join(runDir, "timeout-class-command.mjs");
    writeFileSync(
      timeoutClassCommandPath,
      [
        "#!/usr/bin/env node",
        "const operation = process.argv[5] || \"\";",
        "const delayMs = operation === \"search\" || operation === \"store\" ? 1200 : 20;",
        "setTimeout(() => {",
        "  if (operation === \"search\") {",
        "    process.stdout.write(JSON.stringify({ results: [{ path: \"docs/runbook.md\", snippet: \"Gateway Runbook\" }] }) + \"\\n\");",
        "    process.exit(0);",
        "    return;",
        "  }",
        "  if (operation === \"store\") {",
        "    process.stdout.write(JSON.stringify({ ok: true, operation }) + \"\\n\");",
        "    process.exit(0);",
        "    return;",
        "  }",
        "  process.stdout.write(\"{\\\"ok\\\":true}\\n\");",
        "  process.exit(0);",
        "}, delayMs);",
      ].join("\n"),
      { encoding: "utf8", mode: 0o755 },
    );
    const timeoutClassStub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: [timeoutClassCommandPath],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 20_000,
      startupTimeoutMs: 2_000,
      toolTimeoutMs: 1_500,
      shortTimeoutMs: 1_000,
      longTimeoutMs: 2_000,
    });
    strongclawHypermemoryPlugin.register(timeoutClassStub.api);
    const timeoutClassSearchTool = timeoutClassStub.tools.get("memory_search");
    assert.ok(timeoutClassSearchTool);
    const timeoutClassSearchResult = await timeoutClassSearchTool.execute("timeout-class-search", {
      query: "anything",
    });
    assert.equal(timeoutClassSearchResult.details.disabled, true);
    assert.match(String(timeoutClassSearchResult.details.error), /timed out/i);
    const timeoutClassStoreTool = timeoutClassStub.tools.get("memory_store");
    assert.ok(timeoutClassStoreTool);
    const timeoutClassStoreResult = await timeoutClassStoreTool.execute("timeout-class-store", {
      type: "fact",
      text: "Long timeout should permit writes",
    });
    assert.equal(timeoutClassStoreResult.details.ok, true);

    const stub = createPluginApiStub({
      configPath: memoryConfigPath,
      command: ["uv", "run", "--project", repoRoot, "python", "-m", "clawops"],
      autoRecall: false,
      autoReflect: false,
      timeoutMs: 20_000,
      startupTimeoutMs: 20_000,
      toolTimeoutMs: 20_000,
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
      ["status", "index", "search", "get", "store", "update", "reflect", "forget", "list-facts"],
    );
    const indexCommand = commands[0].subcommands.find((command) => command.name === "index");
    assert.ok(indexCommand?.action);
    await indexCommand.action({ json: true });

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
