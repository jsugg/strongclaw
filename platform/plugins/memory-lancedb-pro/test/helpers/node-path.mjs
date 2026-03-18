import Module from "node:module";

const GLOBAL_NODE_PATHS = [
  "/opt/homebrew/lib/node_modules/openclaw/node_modules",
  "/opt/homebrew/lib/node_modules",
  "/usr/local/lib/node_modules/openclaw/node_modules",
  "/usr/local/lib/node_modules",
  "/usr/lib/node_modules/openclaw/node_modules",
  "/usr/lib/node_modules",
];

export function initGlobalNodePath() {
  process.env.NODE_PATH = [
    process.env.NODE_PATH,
    ...GLOBAL_NODE_PATHS,
  ].filter(Boolean).join(":");
  Module._initPaths();
}
