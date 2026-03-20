export function stripPluginLogs(output) {
  return output
    .split(/\r?\n/)
    .filter((line) => line.trim() && !line.startsWith("[plugins]"))
    .join("\n")
    .trim();
}

export function parseJsonOutput(output) {
  return JSON.parse(stripPluginLogs(output));
}

export function createPluginApiStub(pluginConfig) {
  const tools = new Map();
  const cliHandlers = [];
  const cliRegistrations = [];
  const hooks = [];

  return {
    api: {
      pluginConfig,
      logger: {
        warn() {},
      },
      registerTool(definition) {
        tools.set(definition.name, definition);
      },
      registerCli(handler, options) {
        cliHandlers.push(handler);
        cliRegistrations.push({ handler, options: options ?? {} });
      },
      on(name, handler) {
        hooks.push({ name, handler });
      },
    },
    tools,
    cliHandlers,
    cliRegistrations,
    hooks,
  };
}
