import assert from "node:assert/strict";
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { test } from "node:test";
import {
  applicationEnvironment, execApplication, installerEnvironment, loadBundle, main, parseSetup,
  runChild, runtimePaths,
} from "../lib/launcher.mjs";

function fixture(t, { payload = true } = {}) {
  const directory = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "everval-npm-test-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const packageRoot = path.join(directory, "package with spaces");
  fs.mkdirSync(packageRoot);
  fs.writeFileSync(path.join(packageRoot, "package.json"), JSON.stringify({ version: "0.3.1" }));
  if (payload) {
    fs.mkdirSync(path.join(packageRoot, "runtime"));
    const wheel = Buffer.from("synthetic application wheel");
    const requirements = Buffer.from("example==1.0 --hash=sha256:" + "1".repeat(64) + "\n");
    fs.writeFileSync(path.join(packageRoot, "runtime", "everval_ai-0.3.1-py3-none-any.whl"), wheel);
    fs.writeFileSync(path.join(packageRoot, "runtime", "requirements.txt"), requirements);
    const entry = (filename, bytes) => ({
      path: "runtime/" + filename,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    });
    fs.writeFileSync(path.join(packageRoot, "runtime.json"), JSON.stringify({
      schema_version: 1, distribution: "everval-ai", version: "0.3.1", python: ">=3.12",
      wheel: entry("everval_ai-0.3.1-py3-none-any.whl", wheel),
      requirements: entry("requirements.txt", requirements),
    }));
  }
  const env = {
    HOME: directory,
    EVERVAL_NPM_RUNTIME_DIR: path.join(directory, "private runtimes"),
    EVERVAL_UV: "/approved/uv",
  };
  const calls = [];
  const output = { stdout: "", stderr: "" };
  const options = {
    packageRoot, env, cwd: directory, platform: "linux", architecture: "x64",
    stdout: { write: (value) => { output.stdout += value; } },
    stderr: { write: (value) => { output.stderr += value; } },
    run: async (command, args, settings) => {
      calls.push({ command, args, ...settings });
      if (args.includes("venv")) {
        const venv = args.at(-1);
        fs.mkdirSync(path.join(venv, "bin"), { recursive: true, mode: 0o700 });
        fs.writeFileSync(path.join(venv, "bin", "python"), "synthetic interpreter", { mode: 0o700 });
        fs.writeFileSync(path.join(venv, "pyvenv.cfg"), "home = /approved/python\n");
      }
      return { code: 0, signal: null };
    },
  };
  return { directory, packageRoot, env, calls, output, options };
}

test("help and version work without payload, Python, uv, or creating runtime files", async (t) => {
  const f = fixture(t, { payload: false });
  assert.equal((await main(["--help"], f.options)).code, 0);
  assert.match(f.output.stdout, /everval setup/);
  assert.match(f.output.stdout, /never install/);
  f.output.stdout = "";
  assert.equal((await main(["--version"], f.options)).code, 0);
  assert.equal(f.output.stdout, "0.3.1\n");
  assert.equal(f.calls.length, 0);
  assert.equal(fs.existsSync(f.env.EVERVAL_NPM_RUNTIME_DIR), false);
});

test("normal commands never install or initialize an absent runtime", async (t) => {
  const f = fixture(t);
  assert.equal((await main(["demo"], f.options)).code, 1);
  assert.match(f.output.stderr, /Run everval setup first/);
  assert.equal(f.calls.length, 0);
  assert.equal(fs.existsSync(f.env.EVERVAL_NPM_RUNTIME_DIR), false);
});

test("setup installs exact hashed dependencies, checks them, then publishes readiness", async (t) => {
  const f = fixture(t);
  Object.assign(f.env, {
    UV_NO_VERIFY_HASHES: "1", UV_OVERRIDE: "/unapproved.txt", UV_CONFIG_FILE: "/unapproved.toml",
    UV_VENV_SYSTEM_SITE_PACKAGES: "true", UV_DEFAULT_INDEX: "https://mirror.example/simple",
    PYTHONPATH: "/unapproved", PYTHONHOME: "/unapproved", EVERVAL_PYTHON: "/approved/python3",
  });
  assert.equal((await main(["setup"], f.options)).code, 0);
  assert.equal(f.calls.length, 5);
  const venv = f.calls[0];
  assert.deepEqual(venv.args.slice(0, 7), [
    "--no-config", "--no-python-downloads", "venv", "--no-project", "--python",
    "/approved/python3", venv.args.at(-1),
  ]);
  const install = f.calls[2];
  for (const required of ["--no-build", "--no-deps", "--require-hashes", "--strict"]) {
    assert.ok(install.args.includes(required));
  }
  assert.ok(f.calls[3].args.includes("check"));
  assert.ok(f.calls[3].args.includes("--offline"));
  assert.match(f.calls[4].args.at(-1), /cvals.__version__/);
  for (const call of f.calls) {
    assert.equal(call.env.UV_NO_VERIFY_HASHES, undefined);
    assert.equal(call.env.UV_OVERRIDE, undefined);
    assert.equal(call.env.UV_CONFIG_FILE, undefined);
    assert.equal(call.env.UV_VENV_SYSTEM_SITE_PACKAGES, undefined);
    assert.equal(call.env.PYTHONPATH, undefined);
    assert.equal(call.env.PYTHONHOME, undefined);
    assert.equal(call.env.HOME, f.directory);
    assert.equal(call.env.UV_DEFAULT_INDEX, "https://mirror.example/simple");
    assert.equal(call.env.UV_PYTHON_DOWNLOADS, "never");
  }
  const bundle = loadBundle(f.packageRoot);
  const paths = runtimePaths(bundle, f.env, f.options);
  assert.equal(fs.statSync(paths.root).mode & 0o777, 0o700);
  assert.equal(fs.existsSync(paths.lock), false);
  const marker = JSON.parse(fs.readFileSync(paths.ready));
  assert.equal(marker.manifest_sha256, bundle.digest);
  const appRequirements = fs.readFileSync(
    path.join(paths.directory, "application-requirements.txt"), "utf8",
  );
  assert.match(appRequirements, /file:\/\/\/.*package%20with%20spaces/);
  assert.match(appRequirements, /--hash=sha256:[0-9a-f]{64}/);
  f.calls.length = 0;
  assert.equal((await main(["setup"], f.options)).code, 0);
  assert.equal(f.calls.length, 0, "ready setup must not resolve or reinstall anything");
});

test("offline setup honors caller policy and an existing wheelhouse without network indexes", async (t) => {
  const f = fixture(t);
  f.env.UV_OFFLINE = "true";
  const wheelhouse = path.join(f.directory, "approved wheels");
  fs.mkdirSync(wheelhouse);
  assert.equal((await main(["setup", "--wheelhouse", wheelhouse], f.options)).code, 0);
  assert.ok(f.calls[0].args.includes("--offline"));
  assert.ok(f.calls[2].args.includes("--offline"));
  assert.ok(f.calls[2].args.includes("--no-index"));
  const position = f.calls[2].args.indexOf("--find-links");
  assert.equal(f.calls[2].args[position + 1], wheelhouse);
});

test("setup rejects nonexistent wheelhouses and unknown flags before installing", async (t) => {
  const f = fixture(t);
  for (const args of [["--wheelhouse", "/nonexistent/wheels"], ["--skip-hashes"], ["--wheelhouse"]]) {
    assert.equal((await main(["setup", ...args], f.options)).code, 1);
  }
  assert.throws(() => parseSetup([], { UV_OFFLINE: "maybe" }), /UV_OFFLINE/);
  assert.equal(f.calls.length, 0);
});

test("failed validation leaves an unready environment and refuses silent repair", async (t) => {
  const f = fixture(t);
  const run = f.options.run;
  f.options.run = async (...args) => {
    const result = await run(...args);
    return args[1].includes("check") ? { code: 7, signal: null } : result;
  };
  const result = await main(["setup"], f.options);
  assert.equal(result.code, 7);
  const paths = runtimePaths(loadBundle(f.packageRoot), f.env, f.options);
  assert.equal(fs.existsSync(paths.ready), false);
  assert.equal(fs.existsSync(paths.directory), true);
  assert.equal(fs.existsSync(paths.lock), false);
  const calls = f.calls.length;
  assert.equal((await main(["setup"], f.options)).code, 1);
  assert.match(f.output.stderr, /Inspect it and remove only that directory manually/);
  assert.equal(f.calls.length, calls);
});

test("an existing setup lock is retained and never treated as permission to repair", async (t) => {
  const f = fixture(t);
  const paths = runtimePaths(loadBundle(f.packageRoot), f.env, f.options);
  fs.mkdirSync(paths.root, { mode: 0o700 });
  fs.mkdirSync(paths.lock, { mode: 0o700 });
  assert.equal((await main(["setup"], f.options)).code, 1);
  assert.match(f.output.stderr, /Runtime setup is locked/);
  assert.equal(fs.existsSync(paths.lock), true);
  assert.equal(f.calls.length, 0);
});

test("private runtime roots reject symlinks and permissive existing directories", async (t) => {
  const f = fixture(t);
  const target = path.join(f.directory, "other");
  fs.mkdirSync(target, { mode: 0o700 });
  fs.symlinkSync(target, f.env.EVERVAL_NPM_RUNTIME_DIR);
  assert.equal((await main(["setup"], f.options)).code, 1);
  assert.equal(f.calls.length, 0);
  fs.unlinkSync(f.env.EVERVAL_NPM_RUNTIME_DIR);
  fs.mkdirSync(f.env.EVERVAL_NPM_RUNTIME_DIR, { mode: 0o755 });
  assert.equal((await main(["setup"], f.options)).code, 1);
  assert.equal(f.calls.length, 0);
});

test("payload corruption is rejected before setup and before cached application execution", async (t) => {
  const f = fixture(t);
  assert.equal((await main(["setup"], f.options)).code, 0);
  f.calls.length = 0;
  fs.appendFileSync(path.join(f.packageRoot, "runtime", "requirements.txt"), "# changed\n");
  assert.equal((await main(["demo"], f.options)).code, 1);
  assert.match(f.output.stderr, /integrity check failed/);
  assert.equal((await main(["setup"], f.options)).code, 1);
  assert.equal(f.calls.length, 0);
});

test("payload and manifest paths cannot escape the package or use symbolic links", (t) => {
  const f = fixture(t);
  const manifestPath = path.join(f.packageRoot, "runtime.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath));
  manifest.requirements.path = "../requirements.txt";
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  assert.throws(() => loadBundle(f.packageRoot), /contained directly/);
  manifest.requirements.path = "runtime/requirements.txt";
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  const payload = path.join(f.packageRoot, "runtime", "requirements.txt");
  const target = path.join(f.directory, "requirements.txt");
  fs.renameSync(payload, target);
  fs.symlinkSync(target, payload);
  assert.throws(() => loadBundle(f.packageRoot));
});

test("application preserves exact arguments, working directory, provider environment and exit status", async (t) => {
  const f = fixture(t);
  assert.equal((await main(["setup"], f.options)).code, 0);
  f.calls.length = 0;
  Object.assign(f.env, {
    PYTHONPATH: "/untrusted", PYTHONHOME: "/untrusted",
    EVERVAL_DATA_DIR: "/approved/workspace", CORPORATE_MODEL_KEY: "test-canary",
  });
  f.options.exec = async (command, args, options) => {
    f.calls.push({ command, args, ...options });
    return { code: 19, signal: null };
  };
  const args = ["serve", "--name", "a; literal $(text)\nwith spaces", "--no-worker"];
  assert.equal((await main(args, f.options)).code, 19);
  const call = f.calls[0];
  assert.deepEqual(call.args, ["-I", "-m", "cvals", ...args]);
  assert.match(call.command, /\/venv\/bin\/python$/);
  assert.equal(call.cwd, f.directory);
  assert.equal(call.env.PYTHONPATH, undefined);
  assert.equal(call.env.PYTHONHOME, undefined);
  assert.equal(call.env.HOME, f.directory);
  assert.equal(call.env.CORPORATE_MODEL_KEY, "test-canary");
  assert.equal(call.env.EVERVAL_DATA_DIR, "/approved/workspace");
  assert.doesNotMatch(f.output.stdout + f.output.stderr, /test-canary/);
  f.calls.length = 0;
  await main(["help"], f.options);
  assert.deepEqual(f.calls[0].args, ["-I", "-m", "cvals", "--help"]);
});

test("cache identity follows payload metadata and refuses relative configured paths", (t) => {
  const f = fixture(t);
  const bundle = loadBundle(f.packageRoot);
  const paths = runtimePaths(bundle, f.env, f.options);
  const changed = runtimePaths({ ...bundle, digest: "a".repeat(64) }, f.env, f.options);
  assert.notEqual(paths.directory, changed.directory);
  assert.throws(() => runtimePaths(bundle, { EVERVAL_NPM_RUNTIME_DIR: "relative" }), /absolute/);
  assert.throws(() => runtimePaths(bundle, f.env, { platform: "win32" }), /Linux and macOS/);
});

test("environment filtering preserves approved mirror credentials and ordinary HOME", () => {
  const environment = {
    HOME: "/home/service", UV_INDEX_CORPORATE_PASSWORD: "synthetic",
    UV_INDEX: "https://mirror.example/simple", UV_TARGET: "/unsafe", UV_SYSTEM: "1",
    UV_NO_VERIFY_HASHES: "1", PYTHONHOME: "/unsafe", PYTHONPATH: "/unsafe",
  };
  const installer = installerEnvironment(environment);
  assert.equal(installer.UV_INDEX_CORPORATE_PASSWORD, "synthetic");
  assert.equal(installer.UV_INDEX, environment.UV_INDEX);
  assert.equal(installer.HOME, environment.HOME);
  for (const key of ["UV_TARGET", "UV_SYSTEM", "UV_NO_VERIFY_HASHES", "PYTHONHOME", "PYTHONPATH"]) {
    assert.equal(installer[key], undefined);
  }
  assert.equal(applicationEnvironment(environment).UV_SYSTEM, "1");
});

test("child runner reports real nonzero exits and termination signals without shell evaluation", async () => {
  assert.deepEqual(await runChild(process.execPath, ["-e", "process.exit(23)"]), {
    code: 23, signal: null,
  });
  assert.deepEqual(await runChild(process.execPath, ["-e", "process.kill(process.pid, 'SIGTERM')"]), {
    code: 143, signal: "SIGTERM",
  });
  await assert.rejects(() => runChild("/nonexistent/approved-uv", []), /does not download/);
});


test("invalid interpreters fail before process replacement", (t) => {
  const f = fixture(t);
  const original = process.execve;
  let replaced = false;
  process.execve = () => { replaced = true; };
  try {
    const nonExecutable = path.join(f.directory, "not-executable");
    fs.writeFileSync(nonExecutable, "invalid", { mode: 0o600 });
    assert.throws(() => execApplication(nonExecutable, []));
    assert.throws(() => execApplication(f.directory, []), /regular executable/);
    assert.throws(() => execApplication(path.join(f.directory, "absent"), []));
    assert.equal(replaced, false);
    process.execve = undefined;
    assert.throws(() => execApplication(process.execPath, []), /supported Node version/);
  } finally {
    process.execve = original;
  }
});

test("real PTY handoff preserves PID, tty, input, resize and delivers each signal once", () => {
  const probe = fileURLToPath(new URL("./terminal_probe.py", import.meta.url));
  const launcher = fileURLToPath(new URL("../lib/launcher.mjs", import.meta.url));
  const result = spawnSync("python3", [probe, process.execPath, launcher, "application"], {
    encoding: "utf8", timeout: 20000,
  });
  assert.equal(result.status, 0, result.stderr + result.stdout);
  const record = JSON.parse(result.stdout);
  assert.equal(record.pid, record.launcher_pid);
  assert.equal(record.tty, true);
  assert.deepEqual(record.input, ["hello terminal"]);
  assert.deepEqual(record.dimensions, [43, 121]);
  assert.equal(record.interrupts, 1);
  assert.equal(record.terminations, 1);
  assert.equal(record.exit_code, 0);
  assert.equal(record.experimental_warning, false);
});

test("setup-only child groups receive one terminal interrupt and directed termination", () => {
  const probe = fileURLToPath(new URL("./terminal_probe.py", import.meta.url));
  const launcher = fileURLToPath(new URL("../lib/launcher.mjs", import.meta.url));
  const result = spawnSync("python3", [probe, process.execPath, launcher, "setup"], {
    encoding: "utf8", timeout: 20000,
  });
  assert.equal(result.status, 0, result.stderr + result.stdout);
  const record = JSON.parse(result.stdout);
  assert.notEqual(record.pid, record.launcher_pid);
  assert.equal(record.interrupts, 1);
  assert.equal(record.terminations, 1);
  assert.equal(record.exit_code, 0);
});

test("process replacement preserves the application's actual exit status", () => {
  const launcher = new URL("../lib/launcher.mjs", import.meta.url).href;
  const script = "import {execApplication} from " + JSON.stringify(launcher) +
    "; execApplication(process.execPath, ['-e', 'process.exit(29)']);";
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    encoding: "utf8",
  });
  assert.equal(result.status, 29);
  assert.equal(result.stderr, "");
});
