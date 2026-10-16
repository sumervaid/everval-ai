#!/usr/bin/env node
import { main } from "../lib/launcher.mjs";

const result = await main();
if (result.signal) {
  process.exitCode = result.code;
  process.kill(process.pid, result.signal);
} else {
  process.exitCode = result.code;
}
