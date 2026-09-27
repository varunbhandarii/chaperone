import { cpSync, mkdirSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const dest = join(here, "../public/design");
mkdirSync(join(dest, "fonts"), { recursive: true });
for (const name of ["tokens.css", "logo.svg", "logo-white.svg", "logo-black.svg", "mark.svg", "favicon.svg"]) {
  cpSync(join(here, "../../design", name), join(dest, name));
}
for (const name of [
  "noto-sans-latin.woff2",
  "noto-sans-latin-ext.woff2",
  "noto-sans-devanagari.woff2",
  "noto-sans-mono-latin.woff2",
]) {
  cpSync(join(here, "../../design/fonts", name), join(dest, "fonts", name));
}
cpSync(join(here, "../../contracts/merchants.json"), join(here, "../lib/merchants.json"));
