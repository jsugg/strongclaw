import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const DEFAULT_COMMAND = ["clawops"];
const DEFAULT_TIMEOUT_MS = 20000;
const READ_TIMEOUT_OPERATIONS = new Set(["status", "search", "get", "list-facts"]);
const WRITE_TIMEOUT_OPERATIONS = new Set([
  "index",
  "store",
  "update",
  "reflect",
  "forget",
  "capture",
  "access",
  "record-injection",
  "record-confirmation",
  "record-bad-recall",
  "flush-metadata",
]);
const SEARCH_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: { type: "string" },
    maxResults: { type: "number" },
    minScore: { type: "number" },
    lane: { type: "string", enum: ["all", "memory", "corpus"] },
    scope: { type: "string" },
    backend: { type: "string", enum: ["sqlite_fts", "qdrant_dense_hybrid", "qdrant_sparse_dense_hybrid"] },
    denseCandidatePool: { type: "number" },
    sparseCandidatePool: { type: "number" },
    fusion: { type: "string", enum: ["rrf", "weighted"] },
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
const FORGET_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: { type: "string" },
    entryText: { type: "string" },
    path: { type: "string" },
    hardDelete: { type: "boolean" },
  },
};
const LIST_FACTS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    category: { type: "string" },
    scope: { type: "string" },
  },
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

function clampTimeoutMs(timeoutMs, fallbackTimeoutMs) {
  return timeoutMs && timeoutMs >= 1000 ? Math.min(120000, Math.trunc(timeoutMs)) : fallbackTimeoutMs;
}

function resolvePluginConfig(rawConfig) {
  const input = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  const configPath =
    typeof input.configPath === "string" && input.configPath.trim() ? input.configPath.trim() : "";
  if (!configPath) {
    throw new Error(
      "strongclaw-hypermemory requires plugins.entries.strongclaw-hypermemory.config.configPath",
    );
  }
  if (!existsSync(configPath)) {
    throw new Error(`strongclaw-hypermemory configPath does not exist: ${configPath}`);
  }
  const command =
    Array.isArray(input.command) && input.command.length > 0
      ? input.command.filter((entry) => typeof entry === "string" && entry.trim()).map((entry) => entry.trim())
      : DEFAULT_COMMAND;
  if (command.length === 0) {
    throw new Error("strongclaw-hypermemory command must contain at least one executable");
  }
  const timeoutMs = readNumberParam(input, "timeoutMs");
  const startupTimeoutMs = readNumberParam(input, "startupTimeoutMs");
  const toolTimeoutMs = readNumberParam(input, "toolTimeoutMs");
  const shortTimeoutMs = readNumberParam(input, "shortTimeoutMs");
  const longTimeoutMs = readNumberParam(input, "longTimeoutMs");
  const recallMaxResults = readNumberParam(input, "recallMaxResults");
  const captureMinMessages = readNumberParam(input, "captureMinMessages");
  const resolvedTimeoutMs = clampTimeoutMs(timeoutMs, DEFAULT_TIMEOUT_MS);
  const resolvedStartupTimeoutMs = clampTimeoutMs(startupTimeoutMs, resolvedTimeoutMs);
  const resolvedToolTimeoutMs = clampTimeoutMs(toolTimeoutMs, resolvedTimeoutMs);
  const resolvedShortTimeoutMs = clampTimeoutMs(shortTimeoutMs, resolvedToolTimeoutMs);
  const resolvedLongTimeoutMs = clampTimeoutMs(longTimeoutMs, resolvedToolTimeoutMs);
  return {
    command,
    configPath,
    autoRecall: input.autoRecall === true,
    autoReflect: input.autoReflect === true,
    autoCapture: input.autoCapture === true,
    recallMaxResults:
      recallMaxResults && recallMaxResults >= 1 ? Math.min(10, Math.trunc(recallMaxResults)) : 3,
    captureMinMessages:
      captureMinMessages && captureMinMessages >= 1 ? Math.min(20, Math.trunc(captureMinMessages)) : 4,
    timeoutMs: resolvedTimeoutMs,
    startupTimeoutMs: resolvedStartupTimeoutMs,
    toolTimeoutMs: resolvedToolTimeoutMs,
    shortTimeoutMs: resolvedShortTimeoutMs,
    longTimeoutMs: resolvedLongTimeoutMs,
  };
}

function resolveOperationTimeoutMs(pluginConfig, args, timeoutClass) {
  if (timeoutClass === "short") {
    return pluginConfig.shortTimeoutMs;
  }
  if (timeoutClass === "long") {
    return pluginConfig.longTimeoutMs;
  }
  const operation = Array.isArray(args) && typeof args[0] === "string" ? args[0] : "";
  if (READ_TIMEOUT_OPERATIONS.has(operation)) {
    return pluginConfig.shortTimeoutMs;
  }
  if (WRITE_TIMEOUT_OPERATIONS.has(operation)) {
    return pluginConfig.longTimeoutMs;
  }
  return pluginConfig.toolTimeoutMs;
}

async function runClawopsCommand(
  pluginConfig,
  args,
  { captureJson = true, timeoutMs, timeoutClass, phase = "tool" } = {},
) {
  const [command, ...commandArgs] = pluginConfig.command;
  const fullArgs = [...commandArgs, "hypermemory", "--config", pluginConfig.configPath, ...args];
  const resolvedTimeoutMs = timeoutMs ?? resolveOperationTimeoutMs(pluginConfig, args, timeoutClass);
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
    }, resolvedTimeoutMs);
    if (captureJson) {
      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });
    }
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(
        new Error(
          `failed to start strongclaw-hypermemory command (${command}): ${error instanceof Error ? error.message : String(error)}`,
        ),
      );
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (timedOut) {
        reject(new Error(`clawops hypermemory ${phase} timed out after ${resolvedTimeoutMs}ms`));
        return;
      }
      if (code !== 0) {
        reject(new Error(stderr.trim() || `clawops hypermemory ${phase} exited with code ${code}`));
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
            `failed to parse clawops hypermemory JSON output: ${error instanceof Error ? error.message : String(error)}`,
          ),
        );
      }
    });
  });
}

function createStartupGate(pluginConfig) {
  let startupPromise;
  async function ensureReady() {
    if (startupPromise) {
      return startupPromise;
    }
    startupPromise = runClawopsCommand(pluginConfig, ["status", "--json"], {
      timeoutMs: pluginConfig.startupTimeoutMs,
      phase: "startup preflight",
    }).then((payload) => {
      if (payload && typeof payload === "object" && payload.ok === false) {
        const reason =
          typeof payload.message === "string" && payload.message.trim()
            ? payload.message.trim()
            : "status returned ok=false";
        throw new Error(`strongclaw-hypermemory startup preflight failed: ${reason}`);
      }
    });
    return startupPromise;
  }
  function start(logger) {
    void ensureReady().catch((error) => {
      if (logger && typeof logger.warn === "function") {
        logger.warn(`strongclaw-hypermemory startup preflight failed: ${String(error)}`);
      }
    });
  }
  return { ensureReady, start };
}

function buildDisabledSearchResult(error) {
  return {
    results: [],
    disabled: true,
    unavailable: true,
    error,
    warning: "StrongClaw hypermemory search is unavailable.",
    action: "Verify the strongclaw-hypermemory plugin config and retry memory_search.",
  };
}

function formatRecallContext(results) {
  const lines = ["Relevant StrongClaw hypermemory context:"];
  for (const entry of results) {
    const range =
      entry.startLine === entry.endLine ? `#L${entry.startLine}` : `#L${entry.startLine}-L${entry.endLine}`;
    lines.push(`- ${entry.path}${range}: ${String(entry.snippet || "").trim()}`);
  }
  return lines.join("\n");
}

function fireAndForget(pluginConfig, args, { ensureReady } = {}) {
  const runPromise =
    typeof ensureReady === "function"
      ? Promise.resolve()
          .then(() => ensureReady())
          .then(() => runClawopsCommand(pluginConfig, args))
      : runClawopsCommand(pluginConfig, args);
  runPromise.catch(() => {});
}

function extractSessionMessages(event) {
  const messages = Array.isArray(event?.messages)
    ? event.messages
    : Array.isArray(event?.conversation)
      ? event.conversation
      : [];
  const normalized = [];
  for (let index = 0; index < messages.length; index += 1) {
    const entry = messages[index];
    if (Array.isArray(entry) && entry.length >= 3) {
      normalized.push([Number(entry[0]) || index, String(entry[1] ?? "user"), String(entry[2] ?? "")]);
      continue;
    }
    if (entry && typeof entry === "object") {
      const role = typeof entry.role === "string" ? entry.role : "user";
      const text =
        typeof entry.text === "string"
          ? entry.text
          : typeof entry.content === "string"
            ? entry.content
            : "";
      if (text.trim()) {
        normalized.push([index, role, text]);
      }
    }
  }
  return normalized;
}

function collectInjectedItemIds(results) {
  return Array.isArray(results)
    ? results
        .map((entry) => Number(entry?.itemId))
        .filter((itemId) => Number.isFinite(itemId) && itemId > 0)
    : [];
}

function normalizeTerms(text) {
  return String(text || "")
    .toLowerCase()
    .split(/[^a-z0-9]+/u)
    .filter((term) => term.length >= 4);
}

function extractResponseText(event) {
  if (typeof event?.response === "string") {
    return event.response;
  }
  if (typeof event?.output === "string") {
    return event.output;
  }
  if (typeof event?.text === "string") {
    return event.text;
  }
  return "";
}

function splitFeedbackIds(entries, responseText) {
  const responseTerms = new Set(normalizeTerms(responseText));
  const confirmed = [];
  const badRecall = [];
  for (const entry of entries) {
    const itemId = Number(entry?.itemId);
    if (!Number.isFinite(itemId) || itemId <= 0) {
      continue;
    }
    const snippetTerms = normalizeTerms(entry?.snippet).slice(0, 8);
    if (snippetTerms.length === 0) {
      continue;
    }
    const overlap = snippetTerms.filter((term) => responseTerms.has(term)).length;
    if (overlap / snippetTerms.length >= 0.5) {
      confirmed.push(itemId);
    } else if (responseTerms.size > 0) {
      badRecall.push(itemId);
    }
  }
  return { confirmed, badRecall };
}

function registerMemoryCli(program, runClawops) {
  const memory = program.command("memory").description("Use StrongClaw hypermemory.");
  memory
    .command("status")
    .description("Show StrongClaw hypermemory status.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      await runClawops(["status", ...(opts.json ? ["--json"] : [])], {
        captureJson: false,
      });
    });

  memory
    .command("index")
    .description("Rebuild the StrongClaw hypermemory index.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      await runClawops(["index", ...(opts.json ? ["--json"] : [])], {
        captureJson: false,
      });
    });

  memory
    .command("search [query]")
    .description("Search StrongClaw hypermemory.")
    .option("--query <text>", "Explicit query.")
    .option("--max-results <count>", "Maximum results.")
    .option("--min-score <score>", "Minimum score.")
    .option("--lane <lane>", "memory, corpus, or all.", "all")
    .option("--scope <scope>", "Exact preferred scope.")
    .option("--backend <backend>", "sqlite_fts, qdrant_dense_hybrid, or qdrant_sparse_dense_hybrid.")
    .option("--dense-candidate-pool <count>", "Dense candidate pool override.")
    .option("--sparse-candidate-pool <count>", "Sparse candidate pool override.")
    .option("--fusion <mode>", "rrf or weighted.")
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
      if (opts.backend) {
        args.push("--backend", String(opts.backend));
      }
      if (opts.denseCandidatePool) {
        args.push("--dense-candidate-pool", String(opts.denseCandidatePool));
      }
      if (opts.sparseCandidatePool) {
        args.push("--sparse-candidate-pool", String(opts.sparseCandidatePool));
      }
      if (opts.fusion) {
        args.push("--fusion", String(opts.fusion));
      }
      if (opts.explain) {
        args.push("--explain");
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawops(args, { captureJson: false });
    });

  memory
    .command("get <path>")
    .description("Read a StrongClaw hypermemory file.")
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
      await runClawops(args, { captureJson: false });
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
      await runClawops(args, { captureJson: false });
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
      await runClawops(args, { captureJson: false });
    });

  memory
    .command("reflect")
    .description("Promote retained notes into strongclaw bank files.")
    .option("--mode <mode>", "safe, propose, or apply.", "safe")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["reflect", "--mode", String(opts.mode ?? "safe"), ...(opts.json ? ["--json"] : [])];
      await runClawops(args, {
        captureJson: false,
      });
    });

  memory
    .command("forget")
    .description("Invalidate or delete a durable strongclaw memory entry.")
    .option("--query <text>", "Search query used to resolve the entry.")
    .option("--path <path>", "Workspace-relative path.")
    .option("--entry-text <text>", "Exact entry body text.")
    .option("--hard-delete", "Remove the line instead of soft-invalidating it.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["forget"];
      if (opts.query) {
        args.push("--query", String(opts.query));
      }
      if (opts.path) {
        args.push("--path", String(opts.path));
      }
      if (opts.entryText) {
        args.push("--entry-text", String(opts.entryText));
      }
      if (opts.hardDelete) {
        args.push("--hard-delete");
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawops(args, { captureJson: false });
    });

  memory
    .command("list-facts")
    .description("List canonical fact slots from StrongClaw hypermemory.")
    .option("--category <category>", "profile, preference, decision, or entity.")
    .option("--scope <scope>", "Scope filter.")
    .option("--json", "Print JSON.")
    .action(async (opts) => {
      const args = ["list-facts"];
      if (opts.category) {
        args.push("--category", String(opts.category));
      }
      if (opts.scope) {
        args.push("--scope", String(opts.scope));
      }
      if (opts.json) {
        args.push("--json");
      }
      await runClawops(args, { captureJson: false });
    });
}

const strongclawHypermemoryPlugin = {
  id: "strongclaw-hypermemory",
  name: "StrongClaw Hypermemory",
  description: "Opt-in Markdown-canonical memory plugin backed by clawops hypermemory.",
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
      autoCapture: { type: "boolean" },
      captureMinMessages: { type: "number", minimum: 1, maximum: 20 },
      recallMaxResults: { type: "number", minimum: 1, maximum: 10 },
      timeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
      startupTimeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
      toolTimeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
      shortTimeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
      longTimeoutMs: { type: "number", minimum: 1000, maximum: 120000 },
    },
    required: ["configPath"],
  },
  register(api) {
    const pluginConfig = resolvePluginConfig(api.pluginConfig);
    const sessionFeedback = new Map();
    const sessionKeyFor = (event) => String(event?.sessionId ?? event?.conversationId ?? "default");
    const startupGate = createStartupGate(pluginConfig);
    const ensureReady = startupGate.ensureReady;
    startupGate.start(api.logger);
    const runClawops = async (args, options) => {
      await ensureReady();
      return runClawopsCommand(pluginConfig, args, options);
    };

    api.registerTool(
      {
        name: "memory_search",
        label: "Memory Search",
        description:
          "Search StrongClaw hypermemory over Markdown-canonical memory and configured corpus files.",
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
            const backend = readStringParam(params, "backend");
            if (backend) {
              args.push("--backend", backend);
            }
            const denseCandidatePool = readNumberParam(params, "denseCandidatePool");
            if (denseCandidatePool !== undefined) {
              args.push("--dense-candidate-pool", String(denseCandidatePool));
            }
            const sparseCandidatePool = readNumberParam(params, "sparseCandidatePool");
            if (sparseCandidatePool !== undefined) {
              args.push("--sparse-candidate-pool", String(sparseCandidatePool));
            }
            const fusion = readStringParam(params, "fusion");
            if (fusion) {
              args.push("--fusion", fusion);
            }
            if (params?.explain === true) {
              args.push("--explain");
            }
            const payload = await runClawops(args);
            const injectedIds = collectInjectedItemIds(payload?.results);
            if (injectedIds.length > 0) {
              fireAndForget(
                pluginConfig,
                ["access", "--json", "--item-ids", JSON.stringify(injectedIds)],
                { ensureReady },
              );
            }
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
          "Read a specific workspace-relative file returned by StrongClaw hypermemory search.",
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
            const payload = await runClawops(args);
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
        description: "Append a durable StrongClaw hypermemory entry into the canonical bank files.",
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
            return jsonResult(await runClawops(args));
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
        description: "Replace text inside writable StrongClaw hypermemory Markdown files.",
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
            return jsonResult(await runClawops(args));
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
            return jsonResult(await runClawops(args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_reflect"], optional: true },
    );

    api.registerTool(
      {
        name: "memory_forget",
        label: "Memory Forget",
        description: "Invalidate or delete durable StrongClaw hypermemory entries.",
        parameters: FORGET_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const args = ["forget", "--json"];
            const query = readStringParam(params, "query");
            const entryText = readStringParam(params, "entryText");
            const path = readStringParam(params, "path");
            if (query) {
              args.push("--query", query);
            }
            if (entryText) {
              args.push("--entry-text", entryText);
            }
            if (path) {
              args.push("--path", path);
            }
            if (params?.hardDelete === true) {
              args.push("--hard-delete");
            }
            return jsonResult(await runClawops(args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_forget"], optional: true },
    );

    api.registerTool(
      {
        name: "memory_list_facts",
        label: "Memory List Facts",
        description: "List the current canonical fact slots in StrongClaw hypermemory.",
        parameters: LIST_FACTS_SCHEMA,
        async execute(_toolCallId, params) {
          try {
            const args = ["list-facts", "--json"];
            const category = readStringParam(params, "category");
            const scope = readStringParam(params, "scope");
            if (category) {
              args.push("--category", category);
            }
            if (scope) {
              args.push("--scope", scope);
            }
            return jsonResult(await runClawops(args));
          } catch (error) {
            return jsonResult({ ok: false, error: String(error) });
          }
        },
      },
      { names: ["memory_list_facts"], optional: true },
    );

    api.registerCli(({ program }) => {
      registerMemoryCli(program, runClawops);
    }, { commands: ["memory"] });

    if (pluginConfig.autoRecall) {
      const registerRecall = (hookName) => api.on(hookName, async (event) => {
        const prompt = typeof event?.prompt === "string" ? event.prompt.trim() : "";
        if (prompt.length < 5) {
          return;
        }
        try {
          const payload = await runClawops([
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
          const sessionKey = sessionKeyFor(event);
          sessionFeedback.set(sessionKey, results);
          const injectedIds = collectInjectedItemIds(results);
          if (injectedIds.length > 0) {
            fireAndForget(pluginConfig, [
              "record-injection",
              "--json",
              "--item-ids",
              JSON.stringify(injectedIds),
            ], { ensureReady });
          }
          return {
            prependContext: formatRecallContext(results.slice(0, pluginConfig.recallMaxResults)),
          };
        } catch (error) {
          api.logger.warn(`strongclaw-hypermemory recall failed: ${String(error)}`);
        }
      });
      try {
        registerRecall("before_prompt_build");
      } catch {
        registerRecall("before_agent_start");
      }
      api.on("agent_end", async (event) => {
        const sessionKey = sessionKeyFor(event);
        const injected = sessionFeedback.get(sessionKey);
        if (!Array.isArray(injected) || injected.length === 0) {
          return;
        }
        sessionFeedback.delete(sessionKey);
        const responseText = extractResponseText(event);
        if (!responseText.trim()) {
          return;
        }
        const feedback = splitFeedbackIds(injected, responseText);
        if (feedback.confirmed.length > 0) {
          fireAndForget(pluginConfig, [
            "record-confirmation",
            "--json",
            "--item-ids",
            JSON.stringify(feedback.confirmed),
          ], { ensureReady });
        }
        if (feedback.badRecall.length > 0) {
          fireAndForget(pluginConfig, [
            "record-bad-recall",
            "--json",
            "--item-ids",
            JSON.stringify(feedback.badRecall),
          ], { ensureReady });
        }
      });
    }

    if (pluginConfig.autoCapture) {
      api.on("agent_end", async (event) => {
        try {
          const messages = extractSessionMessages(event);
          if (messages.length < pluginConfig.captureMinMessages) {
            return;
          }
          await runClawops([
            "capture",
            "--json",
            "--messages",
            JSON.stringify(messages),
          ]);
        } catch (error) {
          api.logger.warn(`strongclaw-hypermemory auto-capture failed: ${String(error)}`);
        }
      });
    }

    if (pluginConfig.autoReflect) {
      const runReflect = async (hookName) => {
        try {
          await runClawops(["reflect", "--json"]);
        } catch (error) {
          api.logger.warn(`strongclaw-hypermemory ${hookName} reflect failed: ${String(error)}`);
        }
      };
      api.on("session_end", async () => {
        await runReflect("session_end");
      });
      api.on("before_reset", async () => {
        await runReflect("before_reset");
      });
    }

    api.on("session_end", async () => {
      fireAndForget(pluginConfig, ["flush-metadata", "--json"], { ensureReady });
    });
  },
};

export default strongclawHypermemoryPlugin;
