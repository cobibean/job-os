import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDirectory, "..");
const source = path.join(desktopRoot, "native", "JobOSKeychain.swift");
const outputDirectory = path.join(desktopRoot, "build");
const output = path.join(outputDirectory, "jobos-keychain");

mkdirSync(outputDirectory, { recursive: true });
execFileSync(
  "/usr/bin/xcrun",
  ["swiftc", source, "-framework", "Security", "-o", output],
  { stdio: "inherit" },
);
