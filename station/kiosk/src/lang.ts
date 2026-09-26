// Shopper language: the policy service accepts "es" | "hi" | "en".
// The realtime transcript event may carry a language code; when it does not, a small
// keyword heuristic guesses from the text so /screen and /checkout still get a hint.

export type Lang = "es" | "hi" | "en";

export function normalizeLang(code: unknown): Lang | undefined {
  if (typeof code !== "string") return undefined;
  const base = code.trim().toLowerCase().split(/[-_]/)[0];
  return base === "es" || base === "hi" || base === "en" ? base : undefined;
}

const ES_WORDS = new Set(
  (
    "necesito quiero comprar compra compre pan leche huevos medicina pastillas presion presión para por favor mi mis " +
    "el la los las un una unos unas de del y con sin si sí gracias hola buenos buenas dias días que qué cuanto cuánto " +
    "tarjeta tarjetas regalo dolares dólares nieto nieta hijo hija ahora mismo tambien también esta está es son " +
    "dame deme lo le me necesita agua arroz frijoles azucar azúcar manzanas pollo queso"
  ).split(" "),
);
const HI_ROMAN_WORDS = new Set(
  "mujhe chahiye chaiye hai hain dawai dawa doodh aur kya nahi nahin haan ji roti mera meri mere kripya dijiye lena".split(" "),
);
const EN_WORDS = new Set(
  (
    "i need want buy some bread milk eggs medicine my the a an and please for to of yes no thanks hello " +
    "is are with blood pressure pills gift card cards grandson now this that it can you"
  ).split(" "),
);

export function guessLang(text: string): Lang | undefined {
  const t = (text || "").trim();
  if (!t) return undefined;
  if (/[ऀ-ॿ]/.test(t)) return "hi";
  const words = t.toLowerCase().match(/[\p{L}]+/gu) ?? [];
  let es = /[ñ¿¡áéíóú]/i.test(t) ? 2 : 0;
  let hi = 0;
  let en = 0;
  for (const w of words) {
    if (ES_WORDS.has(w)) es++;
    if (HI_ROMAN_WORDS.has(w)) hi++;
    if (EN_WORDS.has(w)) en++;
  }
  const best = Math.max(es, hi, en);
  if (best === 0) return undefined;
  if (es === best && es > en && es > hi) return "es";
  if (hi === best && hi > en && hi > es) return "hi";
  if (en === best && en > es && en > hi) return "en";
  return undefined;
}

/** Prefer the API's language code; fall back to the text heuristic. */
export function detectLang(apiCode: unknown, text: string): { lang?: Lang; source: "api" | "guess" | "none" } {
  const fromApi = normalizeLang(apiCode);
  if (fromApi) return { lang: fromApi, source: "api" };
  const guessed = guessLang(text);
  return guessed ? { lang: guessed, source: "guess" } : { source: "none" };
}
