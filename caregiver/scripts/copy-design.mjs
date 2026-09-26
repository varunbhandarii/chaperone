import { cpSync, mkdirSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const dest = join(here, "../public/design");
mkdirSync(dest, { recursive: true });
for (const name of ["tokens.css", "logo.svg", "shield.svg"]) {
  cpSync(join(here, "../../design", name), join(dest, name));
}
