import { spawn } from "node:child_process";

const DEFAULT_COMMAND = ["clawops"];
const DEFAULT_TIMEOUT_MS = 20000;
const SEARCH_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: { type: "string" },
    maxResults: { type: "number" },
    minScore: { type: "number" },
    lane: { type: "string", enum: ["all", "memory", "corpus"] },
    scope: { type: "string" },
    explain: { type: "boolean" },
  },
  required: ["query"],
};
const GET_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    path: { type: "string" },
    from: { type: "number" },
    lines: { type: "number" },
  },
  required: ["path"],
};
const STORE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    type: { type: "string", enum: ["fact", "reflection", "opinion", "entity"] },
    text: { type: "string" },
    entity: { type: "string" },
    confidence: { type: "number" },
    scope: { type: "string" },
  },
  required: ["type", "text"],
};
const UPDATE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    path: { type: "string" },
    find: { type: "string" },
    replace: { type: "string" },
    all: { type: "boolean" },
  },
  required: ["path", "find", "replace"],
};
const EMPTY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    mode: { type: "string", enum: ["safe", "propose", "apply"] },
  },
};

function jsonResult(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    details: payload,
  };
}

function readStringParam(params, key, { required = false } = {}) {
  const raw = params?.[key];
  if (typeof raw !== "string" || raw.trim() === "") {
    if (required) {
      throw new Error(`${key} required`);
    }
    return undefined;
  }
  return raw.trim();
}

function readNumberParam(params, key) {
  const raw = params?.[key];
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return raw;
  }
  if (typeof raw === "string" && raw.trim() !== "") {
    const parsed = Number(raw.trim());
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

function resolvePluginConfig(rawConfig) {
  const input = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const configPath =
    typeof input.configPath === "string" && input.configPath.trim() ? input.configPath.trim() : "";
  if (!configPath) {
    throw new Error(
      "strongclaw-memory-v2 requires plugins.entries.strongclaw-memory-v2.config.configPath",
    );
  }
  const command =
    Array.isArray(input.command) && input.command.length > 0
      ? input.command.filter((entry) => typeof entry === "string" && entry.trim()).map((entry) => entry.trim())
      : DEFAULT_COMMAND;
  if (command.length === 0) {
    throw new Error("strongclaw-memory-v2 command must contain at least one executable");
  }
  const timeoutMs = readNumberParam(input, "timeoutMs");
  const recallMaxResults = readNumberParam(input, "recallMaxResults");
  return {
    command,
    configPath,
    autoRecall: input.autoRecall === true,
    autoReflect: input.autoReflect === true,
    recallMaxResults:
      recallMaxResults && recallMaxResults >= 1 ? Math.min(10, Math.trunc(recallMaxResults)) : 3,
    timeoutMs:
      timeoutMs && timeoutMs >= 1000 ? Math.min(120000, Math.trunc(timeoutMs)) : DEFAULT_TIMEOUT_MS,
  };
}

async function runClawopsCommand(pluginConfig, args, { captureJson = true } = {}) {
  const [command, ...commandArgs] = pluginConfig.command;
  const fullArgs = [...commandArgs, "memory-v2", "--config", pluginConfig.configPath, ...args];
  return await new Promise((resolve, reject) => {
    const child = spawn(command, fullArgs, {
      stdio: captureJson ? ["ignore", "pipe", "pipe"] : "inherit",
      env: process.env,
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, pluginConfig.timeoutMs);
    if (captureJson) {
      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });
    }
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      if (timedOut) {
        reject(new Error(`clawops memory-v2 timed out after ${pluginConfig.timeoutMs}ms`));
        return;
      }
      if (code !== 0) {
        reject(new Error(stderr.trim() || `clawops memory-v2 exited with code ${code}`));
        return;
      }
      if (!captureJson) {
        resolve(undefined);
        return;
      }
      try {
        resolve(stdout.trim() ? JSON.parse(stdout) : {});
      } catch (error) {
        reject(
          new Error(
            `failed to parse clawops memory-v2 JSON output: ${error instanceof Error ? error.message : String(error)}`,
          ),
        );
      }
    });
  });
}

function buildDisabledSearchResult(error) {
  return {
    results: [],
    disabled: true,
    unavailable: true,
    error,
    warning: "Strongclaw memory v2 search is unavailable.",
    action: "Verify the strongclaw-memory-v2 plugin config and retry memory_search.",
  };
}

function formatRecallContext(results) {
  const lines = ["Relevant strongclaw memory v2 context:"];
  for (const entry of results) {
    const range =
      entry.startLine === entry.endLine ? `#L${entry.startLine}` : `#L${entry.startLine}-L${entry.endLine}`;
    lines.push(`- ${entry.path}${range}: ${String(entry.snippet || "").trim()}`);
  }
  return lines.join("\n");
}

function registerMemoryCli(program, pluginConfig) {
  const memory = program.command("memory").description("Use strongclaw memory v2.");
  memory
    .command("status")
    .description("Show strongclaw memory v2 status.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      await runClawopsCommand(pluginConfig, ["status", ...(opts.json ? ["--json"] : [])], {
        captureJson: false,
      });
    });

  memory
    .command("index")
    .description("Rebuild the strongclaw memory v2 index.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      await runClawopsCommand(pluginConfig, ["index", ...(opts.json ? ["--json"] : [])], {
        captureJson: false,
      });
    });

  memory
    .command("search [query]")
    .description("Search strongclaw memory v2.")
    .option("--query <text>", "Explicit query.")
    .option("--max-results <count>", "Maximum results.")
    .option("--min-score <score>", "Minimum score.")
    .option("--lane <lane>", "memory, corpus, or all.", "all")
    .option("--scope <scope>", "Exact preferred scope.")
    .option("--explain", "Include ranking explanation metadata.")
    .option("--json", "Print JSON.")
    .action(async (query, opts) => {
      const resolvedQuery =
        (typeof opts.query === "string" && opts.query.trim()) || (typeof query === "string" && query.trim());
      if (!resolvedQuery) {
        throw new Error("query required");
      }
      const args = ["search", "--query", resolvedQuery, "--lane", opts.lane ?? "all"];
      if (opts.maxResults) {
        args.push("--max-results", String(opts.maxResults));
      }
      if (opts.minScore) {
        args.push("--min-score", String(opts.minScore));
      }
      if (opts.scope) {
        args.push("--scope", String(opts.scope));
      }
      if (opts.explain) {
        args.push("--explain");
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawopsCommand(pluginConfig, args, { captureJson: false });
    });

  memory
    .command("get <path>")
    .description("Read a strongclaw memory v2 file.")
    .option("--from <line>", "1-based start line.")
    .option("--lines <count>", "Number of lines.")
    .option("--json", "Print JSON.")
    .action(async (path, opts) => {
      const args = ["get", path];
      if (opts.from) {
        args.push("--from", String(opts.from));
      }
      if (opts.lines) {
        args.push("--lines", String(opts.lines));
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawopsCommand(pluginConfig, args, { captureJson: false });
    });

  memory
    .command("store")
    .description("Append a durable strongclaw memory entry.")
    .requiredOption("--type <type>", "fact, reflection, opinion, or entity")
    .requiredOption("--text <text>", "Entry text.")
    .option("--entity <name>", "Entity name for entity entries.")
    .option("--confidence <score>", "Confidence for opinions.")
    .option("--scope <scope>", "Target scope.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["store", "--type", opts.type, "--text", opts.text];
      if (opts.entity) {
        args.push("--entity", String(opts.entity));
      }
      if (opts.confidence) {
        args.push("--confidence", String(opts.confidence));
      }
      if (opts.scope) {
        args.push("--scope", String(opts.scope));
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawopsCommand(pluginConfig, args, { captureJson: false });
    });

  memory
    .command("update")
    .description("Replace text inside a writable strongclaw memory file.")
    .requiredOption("--path <path>", "Workspace-relative path.")
    .requiredOption("--find <text>", "Text to replace.")
    .requiredOption("--replace <text>", "Replacement text.")
    .option("--all", "Replace all matches.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["update", "--path", opts.path, "--find", opts.find, "--replace", opts.replace];
      if (opts.all) {
        args.push("--all");
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawopsCommand(pluginConfig, args, { captureJson: false });
    });

  memory
    .command("reflect")
    .description("Promote retained notes into strongclaw bank files.")
    .option("--mode <mode>", "safe, propose, or apply.", "safe")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["reflect", "--mode", String(opts.mode ?? "safe"), ...(opts.json ? ["--json"] : [])];
      await runClawopsCommand(pluginConfig, args, {
        captureJson: false,
      });
    });
}

const strongclawMemoryV2Plugin = {
  id: "strongclaw-memory-v2",
  name: "Strongclaw Memory V2",
  description: "Opt-in Markdown-canonical memory plugin backed by clawops memory-v2.",
  kind: "memory",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      configPath: { type: "string" },
      command: {
        type: "array",
        minItems: 1,
        items: { type: "string" },
      },
      autoRecall: { type: "boolean" },
      autoReflect: { type: "boolean" },
      recallMaxResults: { type: "number", minimum: 1, maximum: 10 },
      timeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
    },
    required: ["configPath"],
  },
  register(api) {
    const pluginConfig = resolvePluginConfig(api.pluginConfig);

    api.registerTool(
      {
        name: "memory_search",
        label: "Memory Search",
        description:
          "Search strongclaw memory v2 over Markdown-canonical memory and configured corpus files.",
        parameters: SEARCH_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const query = readStringParam(params, "query", { required: true });
            const maxResults = readNumberParam(params, "maxResults");
            const minScore = readNumberParam(params, "minScore");
            const lane = readStringParam(params, "lane");
            const scope = readStringParam(params, "scope");
            const args = ["search", "--json", "--query", query];
            if (maxResults !== undefined) {
              args.push("--max-results", String(maxResults));
            }
            if (minScore !== undefined) {
              args.push("--min-score", String(minScore));
            }
            if (lane) {
              args.push("--lane", lane);
            }
            if (scope) {
              args.push("--scope", scope);
            }
            if (params?.explain === true) {
              args.push("--explain");
            }
            const payload = await runClawopsCommand(pluginConfig, args);
            return jsonResult(payload);
          } catch (error) {
            return jsonResult(buildDisabledSearchResult(String(error)));
          }
        },
      },
      { names: ["memory_search"] },
    );

    api.registerTool(
      {
        name: "memory_get",
        label: "Memory Get",
        description:
          "Read a specific workspace-relative file returned by strongclaw memory v2 search.",
        parameters: GET_SCHEMA,
        async execute(_toolCallId, params) {
          const path = readStringParam(params, "path", { required: true });
          const args = ["get", "--json", path];
          const fromLine = readNumberParam(params, "from");
          const lines = readNumberParam(params, "lines");
          if (fromLine !== undefined) {
            args.push("--from", String(fromLine));
          }
          if (lines !== undefined) {
            args.push("--lines", String(lines));
          }
          try {
            const payload = await runClawopsCommand(pluginConfig, args);
            return jsonResult(payload);
          } catch (error) {
            return jsonResult({ path, text: "", disabled: true, error: String(error) });
          }
        },
      },
      { names: ["memory_get"] },
    );

    api.registerTool(
      {
        name: "memory_store",
        label: "Memory Store",
        description: "Append a durable strongclaw memory v2 entry into the canonical bank files.",
        parameters: STORE_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const type = readStringParam(params, "type", { required: true });
            const text = readStringParam(params, "text", { required: true });
            const args = ["store", "--json", "--type", type, "--text", text];
            const entity = readStringParam(params, "entity");
            const confidence = readNumberParam(params, "confidence");
            const scope = readStringParam(params, "scope");
            if (entity) {
              args.push("--entity", entity);
            }
            if (confidence !== undefined) {
              args.push("--confidence", String(confidence));
            }
            if (scope) {
              args.push("--scope", scope);
            }
            return jsonResult(await runClawopsCommand(pluginConfig, args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_store"], optional: true },
    );

    api.registerTool(
      {
        name: "memory_update",
        label: "Memory Update",
        description: "Replace text inside writable strongclaw memory v2 Markdown files.",
        parameters: UPDATE_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const path = readStringParam(params, "path", { required: true });
            const findText = readStringParam(params, "find", { required: true });
            const replaceText = readStringParam(params, "replace", { required: true });
            const args = [
              "update",
              "--json",
              "--path",
              path,
              "--find",
              findText,
              "--replace",
              replaceText,
            ];
            if (params?.all === true) {
              args.push("--all");
            }
            return jsonResult(await runClawopsCommand(pluginConfig, args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_update"], optional: true },
    );

    api.registerTool(
      {
        name: "memory_reflect",
        label: "Memory Reflect",
        description: "Promote retained strongclaw notes into typed durable bank files.",
        parameters: EMPTY_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const mode = readStringParam(params, "mode");
            const args = ["reflect", "--json"];
            if (mode) {
              args.push("--mode", mode);
            }
            return jsonResult(await runClawopsCommand(pluginConfig, args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_reflect"], optional: true },
    );

    api.registerCli(({ program }) => {
      registerMemoryCli(program, pluginConfig);
    }, { commands: ["memory"] });

    if (pluginConfig.autoRecall) {
      api.on("before_agent_start", async (event) => {
        const prompt = typeof event?.prompt === "string" ? event.prompt.trim() : "";
        if (prompt.length < 5) {
          return;
        }
        try {
          const payload = await runClawopsCommand(pluginConfig, [
            "search",
            "--json",
            "--query",
            prompt,
            "--max-results",
            String(pluginConfig.recallMaxResults),
          ]);
          const results = Array.isArray(payload?.results) ? payload.results : [];
          if (results.length === 0) {
            return;
          }
          return {
            prependContext: formatRecallContext(results.slice(0, pluginConfig.recallMaxResults)),
          };
        } catch (error) {
          api.logger.warn(`strongclaw-memory-v2 recall failed: ${String(error)}`);
        }
      });
    }

    if (pluginConfig.autoReflect) {
      const runReflect = async (hookName) => {
        try {
          await runClawopsCommand(pluginConfig, ["reflect", "--json"]);
        } catch (error) {
          api.logger.warn(`strongclaw-memory-v2 ${hookName} reflect failed: ${String(error)}`);
        }
      };
      api.on("session_end", async () => {
        await runReflect("session_end");
      });
      api.on("before_reset", async () => {
        await runReflect("before_reset");
      });
    }
  },
};

export default strongclawMemoryV2Plugin;
