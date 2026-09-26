// Renders the same read-back line in several voices so the station voice can be chosen by ear.
//   node station/kiosk/voice_samples.mjs                 # ara, luna, carina (es-MX, en) + naksh, ara (hi) at speed 0.9
//   node station/kiosk/voice_samples.mjs --speed 0.85 --voices ara,luna
//   node station/kiosk/voice_samples.mjs --list          # voices the account can use
// Needs XAI_API_KEY in the repo-root .env. Clips go to station/kiosk/voice-samples/ (gitignored).

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const OUT = join(HERE, "voice-samples");

const LINES = {
  "es-MX":
    "Su pedido: Lisinopril, su medicina de la presión, ocho dólares; y el pan de trigo con miel de siempre, tres dólares con cuarenta y nueve. Total: once dólares con cuarenta y nueve. ¿Hago el pedido?",
  en: "Your order: Lisinopril, your blood pressure medicine, eight dollars; and your usual honey wheat bread, three forty-nine. Total eleven forty-nine. Shall I place the order?",
  hi: "आपका ऑर्डर: लिसिनोप्रिल, आपकी ब्लड प्रेशर की दवाई, आठ डॉलर; और आपकी हमेशा वाली हनी व्हीट ब्रेड, तीन डॉलर उनचास सेंट। कुल ग्यारह डॉलर उनचास सेंट। क्या मैं ऑर्डर कर दूँ?",
};

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

function apiKey() {
  const env = readFileSync(join(ROOT, ".env"), "utf8");
  const key = env.match(/^XAI_API_KEY=(.*)$/m)?.[1]?.trim().replace(/^["']|["']$/g, "");
  if (!key) {
    console.error("XAI_API_KEY is empty in the repo-root .env");
    process.exit(2);
  }
  return key;
}

const key = apiKey();
const headers = { Authorization: `Bearer ${key}`, "Content-Type": "application/json" };

if (process.argv.includes("--list")) {
  const res = await fetch("https://api.x.ai/v1/tts/voices", { headers });
  console.log(res.status, await res.text());
  process.exit(0);
}

const speed = Number(arg("speed", "0.9"));
const voices = arg("voices", "ara,luna,carina").split(",");
const jobs = [];
for (const voice of voices) for (const lang of ["es-MX", "en"]) jobs.push({ voice, lang });
for (const voice of arg("hindi-voices", "naksh,ara").split(",")) jobs.push({ voice, lang: "hi" });

mkdirSync(OUT, { recursive: true });
for (const { voice, lang } of jobs) {
  const t0 = performance.now();
  const res = await fetch("https://api.x.ai/v1/tts", {
    method: "POST",
    headers,
    body: JSON.stringify({ text: LINES[lang], voice_id: voice, language: lang, speed, output_format: { codec: "mp3", sample_rate: 44100, bit_rate: 128000 } }),
  });
  if (!res.ok) {
    console.log(`${voice} ${lang}: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
    continue;
  }
  let audio;
  if ((res.headers.get("content-type") ?? "").startsWith("application/json")) {
    const body = await res.json();
    audio = Buffer.from(body.audio ?? body.data, "base64");
  } else {
    audio = Buffer.from(await res.arrayBuffer());
  }
  const file = join(OUT, `${lang}_${voice}_${speed}.mp3`);
  writeFileSync(file, audio);
  console.log(`${file}  ${Math.round(audio.length / 1024)} KB  ${Math.round(performance.now() - t0)} ms`);
}
