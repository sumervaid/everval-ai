import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const PACKAGE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHA256 = /^[0-9a-f]{64}$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;
const INSTALLER_ENV = new Set([
  "UV_DEFAULT_INDEX", "UV_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL",
  "UV_INDEX_STRATEGY", "UV_CACHE_DIR", "UV_SYSTEM_CERTS", "UV_NATIVE_TLS",
  "UV_HTTP_TIMEOUT", "UV_HTTP_CONNECT_TIMEOUT", "UV_HTTP_RETRIES",
]);

function failure(message) {
  return new Error(message);
}

function regularFile(filename, maximum = 256 * 1024 * 1024) {
  const descriptor = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const info = fs.fstatSync(descriptor);
    if (!info.isFile() || info.size > maximum) {
      throw failure("The package contains an invalid or oversized runtime file.");
    }
    return fs.readFileSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
}

export function readPackageVersion(packageRoot = PACKAGE_ROOT) {
  const metadata = JSON.parse(regularFile(path.join(packageRoot, "package.json"), 1024 * 1024));
  if (typeof metadata.version !== "string" || !VERSION.test(metadata.version)) {
    throw failure("The npm package has an invalid version.");
  }
  return metadata.version;
}

function payloadFile(packageRoot, entry, suffix) {
  if (!entry || typeof entry.path !== "string" || !SHA256.test(entry.sha256 ?? "")) {
    throw failure("The runtime manifest has an invalid payload entry.");
  }
  const parts = entry.path.split("/");
  if (parts.length !== 2 || parts[0] !== "runtime" ||
      !/^[A-Za-z0-9_.-]+$/.test(parts[1]) || !parts[1].endsWith(suffix)) {
    throw failure("Runtime payload paths must be contained directly under runtime/.");
  }
  const directory = path.join(packageRoot, "runtime");
  if (!fs.lstatSync(directory).isDirectory()) {
    throw failure("The runtime payload directory must not be a symbolic link.");
  }
  const filename = path.join(directory, parts[1]);
  const bytes = regularFile(filename);
  if (createHash("sha256").update(bytes).digest("hex") !== entry.sha256) {
    throw failure("Runtime payload integrity check failed. Reinstall the reviewed npm package.");
  }
  return filename;
}

export function loadBundle(packageRoot = PACKAGE_ROOT) {
  const version = readPackageVersion(packageRoot);
  const bytes = regularFile(path.join(packageRoot, "runtime.json"), 64 * 1024);
  const manifest = JSON.parse(bytes);
  if (manifest.schema_version !== 1 || manifest.distribution !== "everval-ai" ||
      manifest.version !== version || manifest.python !== ">=3.12") {
    throw failure("The runtime manifest does not match this npm release.");
  }
  return {
    version,
    digest: createHash("sha256").update(bytes).digest("hex"),
    wheel: payloadFile(packageRoot, manifest.wheel, ".whl"),
    wheelSha256: manifest.wheel.sha256,
    requirements: payloadFile(packageRoot, manifest.requirements, ".txt"),
  };
}

function absoluteDirectory(value, name) {
  if (typeof value !== "string" || !value || !path.isAbsolute(value) || value.includes("\0")) {
    throw failure(name + " must be a nonempty absolute directory path.");
  }
  return path.normalize(value);
}

export function runtimePaths(bundle, environment = process.env, options = {}) {
  const platform = options.platform ?? process.platform;
  const architecture = options.architecture ?? process.arch;
  if (!["darwin", "linux"].includes(platform)) {
    throw failure("Everval's Python application currently supports Linux and macOS.");
  }
  const home = options.home ?? os.homedir();
  const defaultCache = platform === "darwin"
    ? path.join(home, "Library", "Caches")
    : path.join(home, ".cache");
  const cache = environment.XDG_CACHE_HOME
    ? absoluteDirectory(environment.XDG_CACHE_HOME, "XDG_CACHE_HOME")
    : defaultCache;
  const root = absoluteDirectory(
    environment.EVERVAL_NPM_RUNTIME_DIR ?? path.join(cache, "everval", "npm"),
    "EVERVAL_NPM_RUNTIME_DIR",
  );
  const key = platform + "-" + architecture + "-" + bundle.digest;
  const directory = path.join(root, key);
  return {
    root, key, directory,
    lock: path.join(root, key + ".lock"),
    venv: path.join(directory, "venv"),
    python: path.join(directory, "venv", "bin", "python"),
    ready: path.join(directory, "ready.json"),
  };
}

// Every ancestor must be controlled by this user or root. A root-owned sticky
// system temporary directory is allowed, but the runtime itself is private.
function inspectDirectory(directory, privateDirectory = false) {
  const info = fs.lstatSync(directory);
  const uid = process.getuid();
  if (!info.isDirectory()) {
    throw failure("Runtime directories must not be symbolic links: " + directory);
  }
  if (privateDirectory) {
    if (info.uid !== uid || (info.mode & 0o077) !== 0 || (info.mode & 0o700) !== 0o700) {
      throw failure("Runtime directories must be owned by this user with mode 0700: " + directory);
    }
  } else {
    const protectedTemporary = info.uid === 0 && (info.mode & 0o1000) !== 0;
    if (![0, uid].includes(info.uid) || ((info.mode & 0o022) !== 0 && !protectedTemporary)) {
      throw failure("Runtime parent directory is writable by another user: " + directory);
    }
  }
}

function ensureDirectoryTree(directory, create) {
  const parsed = path.parse(directory);
  let current = parsed.root;
  inspectDirectory(current);
  for (const component of directory.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    try {
      inspectDirectory(current);
    } catch (error) {
      if (error.code !== "ENOENT" || !create) throw error;
      fs.mkdirSync(current, { mode: 0o700 });
      inspectDirectory(current);
    }
  }
  inspectDirectory(directory, true);
}

function readyRuntime(bundle, paths) {
  ensureDirectoryTree(paths.root, false);
  inspectDirectory(paths.directory, true);
  const marker = JSON.parse(regularFile(paths.ready, 8192));
  if (marker.schema_version !== 1 || marker.manifest_sha256 !== bundle.digest ||
      marker.version !== bundle.version || marker.key !== paths.key) {
    throw failure("The installed runtime marker does not match this release.");
  }
  inspectDirectory(paths.venv);
  inspectDirectory(path.join(paths.venv, "bin"));
  regularFile(path.join(paths.venv, "pyvenv.cfg"), 64 * 1024);
  // Keep the venv path: resolving the interpreter symlink loses its environment.
  if (!fs.statSync(paths.python).isFile()) throw failure("The installed runtime is incomplete.");
  return paths.python;
}

export function applicationEnvironment(environment = process.env) {
  const result = { ...environment };
  delete result.PYTHONPATH;
  delete result.PYTHONHOME;
  return result;
}

export function installerEnvironment(environment = process.env) {
  const result = applicationEnvironment(environment);
  for (const key of Object.keys(result)) {
    if (key.startsWith("UV_") && !INSTALLER_ENV.has(key) &&
        !/^UV_INDEX_[A-Z0-9_]+_(USERNAME|PASSWORD)$/.test(key)) {
      delete result[key];
    }
  }
  result.UV_PYTHON_DOWNLOADS = "never";
  return result;
}

function isOffline(environment) {
  const value = (environment.UV_OFFLINE ?? "").toLowerCase();
  if (!["", "0", "1", "true", "false"].includes(value)) {
    throw failure("UV_OFFLINE must be true, false, 1, or 0.");
  }
  return ["1", "true"].includes(value);
}

export function parseSetup(args, environment = process.env, cwd = process.cwd()) {
  let offline = isOffline(environment);
  let wheelhouse;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--offline") {
      offline = true;
    } else if (args[index] === "--wheelhouse" && args[index + 1] &&
               !args[index + 1].startsWith("-")) {
      if (wheelhouse) throw failure("--wheelhouse may be supplied only once.");
      wheelhouse = path.resolve(cwd, args[++index]);
      if (!fs.statSync(wheelhouse).isDirectory()) {
        throw failure("--wheelhouse must select an existing local directory.");
      }
    } else {
      throw failure("Usage: everval setup [--offline] [--wheelhouse PATH]");
    }
  }
  return { offline, wheelhouse };
}

export function runChild(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? process.cwd(),
      env: options.env ?? process.env,
      // Setup is noninteractive. A separate group receives terminal signals
      // only through this parent, once, including any installer descendants.
      stdio: ["ignore", "inherit", "inherit"],
      shell: false,
      detached: true,
    });
    const listeners = new Map();
    for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
      const listener = () => {
        if (child.pid === undefined) return;
        try {
          process.kill(-child.pid, signal);
        } catch (error) {
          if (error.code !== "ESRCH") throw error;
        }
      };
      listeners.set(signal, listener);
      process.on(signal, listener);
    }
    function cleanup() {
      for (const [signal, listener] of listeners) process.off(signal, listener);
    }
    child.once("error", (error) => {
      cleanup();
      if (error.code === "ENOENT") {
        reject(failure("Required executable was not found: " + command +
          ". Install an approved uv and Python 3.12+; the launcher does not download them."));
      } else {
        reject(failure("Could not start the requested runtime process (" + error.code + ")."));
      }
    });
    child.once("exit", (code, signal) => {
      cleanup();
      resolve({ code: code ?? 128 + (os.constants.signals[signal] ?? 1), signal });
    });
  });
}

export function execApplication(command, args, options = {}) {
  if (typeof process.execve !== "function") {
    throw failure("This Node release cannot preserve terminal behavior. Use a supported Node version.");
  }
  // Validate before execve: Node releases before 26.1 abort on a failed
  // underlying syscall. Do not resolve the venv interpreter's symbolic link.
  if (!fs.statSync(command).isFile()) {
    throw failure("The installed Python interpreter is not a regular executable file.");
  }
  fs.accessSync(command, fs.constants.X_OK);
  if (options.cwd && options.cwd !== process.cwd()) process.chdir(options.cwd);
  // Replacing Node preserves PID, foreground process group, /dev/tty, resize
  // events and exact exit status. There is no proxy to duplicate Ctrl+C.
  process.execve(command, [command, ...args], options.env ?? process.env);
  throw failure("Could not replace the launcher with the installed application.");
}

function installCommands(bundle, paths, setup, environment) {
  const uv = environment.EVERVAL_UV || "uv";
  const python = environment.EVERVAL_PYTHON || ">=3.12";
  const network = setup.offline ? ["--offline", "--no-index"] : [];
  const shared = ["--no-config", "--no-python-downloads"];
  const requirements = path.join(paths.directory, "application-requirements.txt");
  return [
    [uv, [...shared, "venv", "--no-project", "--python", python,
      ...(setup.offline ? ["--offline"] : []), paths.venv]],
    [paths.python, ["-I", "-c",
      "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"]],
    [uv, [...shared, "pip", "install", "--python", paths.python,
      "--no-build", "--no-deps", "--require-hashes", "--strict", "--link-mode", "copy",
      ...network, ...(setup.wheelhouse ? ["--find-links", setup.wheelhouse] : []),
      "--requirement", bundle.requirements, "--requirement", requirements]],
    [uv, [...shared, "pip", "check", "--python", paths.python, "--offline"]],
    [paths.python, ["-I", "-c",
      "import cvals; assert cvals.__version__ == " + JSON.stringify(bundle.version)]],
  ];
}

export async function setupRuntime(bundle, paths, setup, options = {}) {
  const environment = options.env ?? process.env;
  const run = options.run ?? runChild;
  const cwd = options.cwd ?? process.cwd();
  ensureDirectoryTree(paths.root, true);
  try {
    fs.mkdirSync(paths.lock, { mode: 0o700 });
  } catch (error) {
    if (error.code === "EEXIST") {
      throw failure("Runtime setup is locked: " + paths.lock +
        ". Wait for the other setup. Inspect stale locks manually; they are never removed automatically.");
    }
    throw error;
  }
  try {
    try {
      readyRuntime(bundle, paths);
      return { code: 0, signal: null, alreadyInstalled: true };
    } catch (error) {
      if (fs.existsSync(paths.directory) || fs.lstatSync(paths.root).isSymbolicLink()) {
        throw failure("An incomplete or unverified runtime already exists: " + paths.directory +
          ". Inspect it and remove only that directory manually before retrying.");
      }
      if (error.code !== "ENOENT") throw error;
    }
    fs.mkdirSync(paths.directory, { mode: 0o700 });
    fs.writeFileSync(path.join(paths.directory, "application-requirements.txt"),
      "everval-ai @ " + pathToFileURL(bundle.wheel).href +
      " --hash=sha256:" + bundle.wheelSha256 + "\n", { mode: 0o600, flag: "wx" });
    const childEnvironment = installerEnvironment(environment);
    for (const [command, args] of installCommands(bundle, paths, setup, environment)) {
      const result = await run(command, args, { cwd, env: childEnvironment });
      if (result.code !== 0 || result.signal) {
        return { ...result, incomplete: paths.directory };
      }
    }
    const temporary = path.join(paths.directory, ".ready-" + randomUUID());
    fs.writeFileSync(temporary, JSON.stringify({
      schema_version: 1,
      version: bundle.version,
      manifest_sha256: bundle.digest,
      key: paths.key,
    }) + "\n", { mode: 0o600, flag: "wx" });
    fs.renameSync(temporary, paths.ready);
    readyRuntime(bundle, paths);
    return { code: 0, signal: null, alreadyInstalled: false };
  } finally {
    // Only the empty lock created by this invocation is removed. Partial
    // environments and pre-existing locks require deliberate operator action.
    fs.rmdirSync(paths.lock);
  }
}

function help(version) {
  return `Everval AI ${version}
On-prem continuous benchmarking for AI models and agents.

Usage:
  everval setup [--offline] [--wheelhouse PATH]   Install the bundled Python runtime
  everval help                                  Show application commands after setup
  everval demo                                  Run the provider-free walkthrough
  everval tui                                   Open the terminal workbench
  everval serve --no-worker                      Open the local review application
  everval <command> [arguments]                  Forward arguments to the application

Install approved uv and Python 3.12+ before setup. Node does not install them.
Setup uses locked, hashed packages; normal commands never install dependencies.
Use UV_DEFAULT_INDEX for an internal Python mirror, or setup --offline with
--wheelhouse /approved/wheels.
EVERVAL_NPM_RUNTIME_DIR selects a private absolute runtime directory.
EVERVAL_UV and EVERVAL_PYTHON select existing uv/Python executables.
Workspace data and provider settings use the application's EVERVAL_* options.
--help and --version do not need Python or network access.
`;
}

export async function main(args = process.argv.slice(2), options = {}) {
  const packageRoot = options.packageRoot ?? PACKAGE_ROOT;
  const environment = options.env ?? process.env;
  const stdout = options.stdout ?? process.stdout;
  const stderr = options.stderr ?? process.stderr;
  const cwd = options.cwd ?? process.cwd();
  try {
    const version = readPackageVersion(packageRoot);
    if (args.length === 0 || (args.length === 1 && ["--help", "-h"].includes(args[0]))) {
      stdout.write(help(version));
      return { code: 0, signal: null };
    }
    if (args.length === 1 && args[0] === "--version") {
      stdout.write(version + "\n");
      return { code: 0, signal: null };
    }
    const bundle = loadBundle(packageRoot);
    const paths = runtimePaths(bundle, environment, options);
    if (args[0] === "setup") {
      const setup = parseSetup(args.slice(1), environment, cwd);
      const result = await setupRuntime(bundle, paths, setup, options);
      if (result.code === 0 && !result.signal) {
        stdout.write("Everval runtime ready. Run everval help or everval demo.\n");
      } else {
        stderr.write("Setup did not complete; no runtime was marked ready. Inspect " +
          paths.directory + " before retrying.\n");
      }
      return result;
    }
    let python;
    try {
      python = readyRuntime(bundle, paths);
    } catch {
      throw failure("This release has no verified runtime. Run everval setup first. " +
        "Runtime location: " + paths.directory);
    }
    const forwarded = args[0] === "help" ? ["--help", ...args.slice(1)] : args;
    return await (options.exec ?? execApplication)(python, ["-I", "-m", "cvals", ...forwarded], {
      cwd, env: applicationEnvironment(environment),
    });
  } catch (error) {
    const message = error instanceof SyntaxError
      ? "The package contains malformed metadata. Reinstall the reviewed release."
      : error.message;
    stderr.write("Everval: " + message + "\n");
    return { code: 1, signal: null };
  }
}
