// Words Ruth reads on the screen, in her language (the spoken lines are in cart.ts): the big button and the state
// strip, the page's labels, the stage, the Protected card, the co-sign, the order outcome and the notices after payment.
// Money on the screen is always digits ("$480.00"), in every language; the spoken forms stay in the voice.
// Pure data: no DOM or network, so it runs under `node --test`.

import type { AgentState } from "./agent.ts";
import type { CheckoutOutcome } from "./cart.ts";
import type { Lang } from "./lang.ts";

/** One state at a time, in words, in Ruth's language: the big button (and the operator's strip) say it. */
export const STATE_WORDS: Record<Lang, Record<AgentState, string>> = {
  en: {
    off: "Tap to start",
    connecting: "Getting ready…",
    ready: "Hold to talk",
    listening: "Listening…",
    thinking: "One moment…",
    checking: "Checking…",
    speaking: "Speaking…",
    waiting: "Asking Priyank…",
  },
  es: {
    off: "Toque para empezar",
    connecting: "Preparando…",
    ready: "Mantenga presionado para hablar",
    listening: "Escuchando…",
    thinking: "Un momento…",
    checking: "Revisando…",
    speaking: "Hablando…",
    waiting: "Preguntando a Priyank…",
  },
  hi: {
    off: "शुरू करने के लिए छुएँ",
    connecting: "तैयार हो रही हूँ…",
    ready: "बोलने के लिए दबाकर रखें",
    listening: "सुन रही हूँ…",
    thinking: "एक पल…",
    checking: "जाँच रही हूँ…",
    speaking: "बोल रही हूँ…",
    waiting: "प्रियंक से पूछ रही हूँ…",
  },
};

export const TOLD: Record<Lang, string> = { en: "Priyank has been told", es: "Priyank ya lo sabe", hi: "प्रियंक को बता दिया गया है" };
export const TITLES: Record<Lang, { protected: string; care: string }> = {
  en: { protected: "Protected", care: "Be careful" },
  es: { protected: "Protegida", care: "Tenga cuidado" },
  hi: { protected: "सुरक्षित", care: "सावधान रहें" },
};
export const NOTHING_YET: Record<Lang, string> = { en: "Nothing yet", es: "Nada todavía", hi: "अभी कुछ नहीं" };
/** A store with no name to give, said and shown in a line ("I stopped a charge at the store"). */
export const THE_STORE: Record<Lang, string> = { en: "the store", es: "la tienda", hi: "दुकान" };

/** The order outcome's title; the line under it is what she heard. */
export const OUTCOME_TITLES: Record<Lang, Record<CheckoutOutcome["status"], string>> = {
  en: { ordered: "Ordered", waiting_for_caregiver: "Asking Priyank", declined: "Not ordered", error: "Order not placed" },
  es: { ordered: "Pedido hecho", waiting_for_caregiver: "Preguntando a Priyank", declined: "No se pidió", error: "No se pudo hacer el pedido" },
  hi: { ordered: "ऑर्डर हो गया", waiting_for_caregiver: "प्रियंक से पूछ रही हूँ", declined: "ऑर्डर नहीं किया गया", error: "ऑर्डर नहीं हो पाया" },
};

export type PageWord =
  | "your_order"
  | "total"
  | "conversation"
  | "found"
  | "type_instead"
  | "type_here"
  | "send"
  | "reprint"
  | "close"
  | "hold_to_talk"
  | "you"
  | "usual"
  | "no_matches"
  | "interrupted"
  | "nothing_heard"
  | "you_can_say"
  | "left_month"
  | "needs_priya"
  | "waiting_priya"
  | "new_rules"
  | "nothing_changes"
  | "signed_by"
  | "scan";

/** The page's fixed labels (elements marked data-words="<key>") and the small words in the lists. */
export const PAGE_WORDS: Record<Lang, Record<PageWord, string>> = {
  en: {
    your_order: "Your order",
    total: "Total",
    conversation: "Conversation",
    found: "I found",
    type_instead: "Type instead of speaking",
    type_here: "Type here",
    send: "Send",
    reprint: "Print again",
    close: "Close",
    hold_to_talk: "hold to talk",
    you: "You",
    usual: "Your usual",
    no_matches: "No matches",
    interrupted: "(interrupted)",
    nothing_heard: "(nothing heard)",
    you_can_say: "You can say",
    left_month: "Left to spend this month",
    needs_priya: "Needs Priyank's okay",
    waiting_priya: "Waiting for Priyank",
    new_rules: "New rules from Priyank",
    nothing_changes: "Nothing changes until you say yes.",
    signed_by: "Signed by Priyank",
    scan: "Scan for your session",
  },
  es: {
    your_order: "Su pedido",
    total: "Total",
    conversation: "Conversación",
    found: "Encontré",
    type_instead: "Escriba en lugar de hablar",
    type_here: "Escriba aquí",
    send: "Enviar",
    reprint: "Imprimir otra vez",
    close: "Cerrar",
    hold_to_talk: "mantenga presionado para hablar",
    you: "Usted",
    usual: "Lo de siempre",
    no_matches: "No encontré nada",
    interrupted: "(interrumpido)",
    nothing_heard: "(no se oyó nada)",
    you_can_say: "Puede decir",
    left_month: "Le queda para gastar este mes",
    needs_priya: "Necesita el visto bueno de Priyank",
    waiting_priya: "Esperando a Priyank",
    new_rules: "Reglas nuevas de Priyank",
    nothing_changes: "Nada cambia hasta que usted diga que sí.",
    signed_by: "Firmado por Priyank",
    scan: "Escanee para ver su sesión",
  },
  hi: {
    your_order: "आपका ऑर्डर",
    total: "कुल",
    conversation: "बातचीत",
    found: "मुझे मिला",
    type_instead: "बोलने के बजाय लिखें",
    type_here: "यहाँ लिखें",
    send: "भेजें",
    reprint: "फिर से छापें",
    close: "बंद करें",
    hold_to_talk: "बोलने के लिए दबाकर रखें",
    you: "आप",
    usual: "आपका हमेशा वाला",
    no_matches: "कुछ नहीं मिला",
    interrupted: "(बीच में रुका)",
    nothing_heard: "(कुछ सुनाई नहीं दिया)",
    you_can_say: "आप कह सकती हैं",
    left_month: "इस महीने ख़र्च के लिए बाक़ी",
    needs_priya: "प्रियंक की मंज़ूरी चाहिए",
    waiting_priya: "प्रियंक का इंतज़ार",
    new_rules: "प्रियंक के नए नियम",
    nothing_changes: "आपके हाँ कहने तक कुछ नहीं बदलेगा।",
    signed_by: "प्रियंक ने हस्ताक्षर किए",
    scan: "अपना सत्र देखने के लिए स्कैन करें",
  },
};

/** The small line under the big button's words, by what Ruth can do now. The station's push-to-talk key is added
 * in the operator view only. */
export type ButtonHint = "off" | "ready" | "listening" | "speaking" | "waiting" | "read_back" | "cosign";
export const BUTTON_HINTS: Record<Lang, Record<ButtonHint, string>> = {
  en: {
    off: "then hold it to talk",
    ready: "or hold the button on your desk",
    listening: "let go when you're done",
    speaking: "hold to interrupt",
    waiting: "you can keep shopping while Priyank answers",
    read_back: "or just say “yes”",
    cosign: "or say “Yes, I agree”",
  },
  es: {
    off: "y luego manténgalo presionado para hablar",
    ready: "o mantenga presionado el botón de su mesa",
    listening: "suelte cuando termine",
    speaking: "mantenga presionado para interrumpir",
    waiting: "puede seguir comprando mientras Priyank responde",
    read_back: "o solo diga “sí”",
    cosign: "o diga “Sí, estoy de acuerdo”",
  },
  hi: {
    off: "फिर बोलने के लिए दबाकर रखें",
    ready: "या अपनी मेज़ वाला बटन दबाकर रखें",
    listening: "बोलकर छोड़ दें",
    speaking: "बीच में बोलने के लिए दबाकर रखें",
    waiting: "प्रियंक के जवाब तक आप ख़रीदारी जारी रख सकती हैं",
    read_back: "या बस “हाँ” कहिए",
    // the co-sign check hears "हाँ, सहमत" as a yes; "मैं … हूँ" around it is not a yes word to it
    cosign: "या कहिए “हाँ, सहमत”",
  },
};

/** A choice Ruth can tap: the words on the button, and what is sent through the typed-input path (so every guard
 * applies). The sent words read the same to the read-back, the co-sign check (guards.yesOrNo) and the language
 * guess as if she had said them. */
export interface Choice {
  label: string;
  send: string;
}

/** The stage: the greeting before anything is said, the read-back and co-sign choices, the wait for Priyank, the
 * orders placed, and a calm line when the station cannot hear her (never the technical reason). */
export interface StageWords {
  greeting: (hour: number) => string;
  ask: string;
  examples: string[];
  yes_place: Choice;
  no_place: Choice;
  sent_at: (time: string) => string;
  placed: (count: number, allBills: boolean) => string;
  cosign_title: string;
  cosign_yes: Choice;
  cosign_no: Choice;
  signed_when: (time: string) => string;
  items: (n: number) => string;
  calm: { no_mic: string; trouble: string; start_first: string };
}

const EN_COUNT = ["", "One", "Two", "Three", "Four", "Five"];
const ES_COUNT = ["", "Un", "Dos", "Tres", "Cuatro", "Cinco"];
const HI_COUNT = ["", "एक", "दो", "तीन", "चार", "पाँच"];

export const STAGE_WORDS: Record<Lang, StageWords> = {
  en: {
    greeting: (h) => (h >= 4 && h < 12 ? "Good morning, Ruth." : h >= 12 && h < 17 ? "Good afternoon, Ruth." : "Good evening, Ruth."),
    ask: "What can I do for you?",
    examples: ["“I need my blood pressure medicine.”", "“Pay my power bill.”", "“Someone called asking me for money.”"],
    yes_place: { label: "Yes, place it", send: "Yes" },
    no_place: { label: "No", send: "No" },
    sent_at: (time) => `Sent to his phone at ${time}`,
    placed: (n, bills) => (n <= 1 ? (bills ? "Done. Your bill is paid." : "Done. Your order is placed.") : `Done. ${EN_COUNT[n] ?? n} orders placed.`),
    cosign_title: "Priyank set your rules. Do you agree?",
    cosign_yes: { label: "Yes, I agree", send: "Yes, I agree" },
    cosign_no: { label: "Not now", send: "Not now" },
    signed_when: (time) => `${time}, with his passkey`,
    items: (n) => (n === 1 ? "1 item" : `${n} items`),
    calm: {
      no_mic: "I can't hear you right now. You can type below.",
      trouble: "Something went wrong on my side. Tap the big button to start again.",
      start_first: "Tap the big button to start first.",
    },
  },
  es: {
    greeting: (h) => (h >= 4 && h < 12 ? "Buenos días, Ruth." : h >= 12 && h < 19 ? "Buenas tardes, Ruth." : "Buenas noches, Ruth."),
    ask: "¿En qué le puedo ayudar?",
    examples: ["“Necesito mi medicina para la presión.”", "“Pague mi factura de la luz.”", "“Alguien me llamó pidiéndome dinero.”"],
    yes_place: { label: "Sí, hágalo", send: "Sí" },
    // "No" alone reads as English to the language guess; "No, gracias" keeps her Spanish
    no_place: { label: "No", send: "No, gracias" },
    sent_at: (time) => `Enviado a su teléfono a las ${time}`,
    placed: (n, bills) =>
      n <= 1 ? (bills ? "Listo. Su factura está pagada." : "Listo. Su pedido está hecho.") : `Listo. ${ES_COUNT[n] ?? n} pedidos hechos.`,
    cosign_title: "Priyank puso sus reglas. ¿Está de acuerdo?",
    cosign_yes: { label: "Sí, estoy de acuerdo", send: "Sí, estoy de acuerdo" },
    cosign_no: { label: "Ahora no", send: "Ahora no" },
    signed_when: (time) => `${time}, con su llave de acceso`,
    items: (n) => (n === 1 ? "1 artículo" : `${n} artículos`),
    calm: {
      no_mic: "Ahora no la puedo oír. Puede escribir abajo.",
      trouble: "Algo falló de mi lado. Toque el botón grande para empezar de nuevo.",
      start_first: "Primero toque el botón grande para empezar.",
    },
  },
  hi: {
    greeting: () => "नमस्ते, रूथ जी।",
    ask: "मैं आपकी क्या मदद करूँ?",
    examples: ["“मुझे मेरी बीपी की दवा चाहिए।”", "“मेरा बिजली का बिल भर दो।”", "“किसी ने फ़ोन करके पैसे माँगे।”"],
    yes_place: { label: "हाँ, ऑर्डर कर दो", send: "हाँ" },
    no_place: { label: "नहीं", send: "नहीं" },
    sent_at: (time) => `${time} पर उनके फ़ोन पर भेजा`,
    placed: (n, bills) => (n <= 1 ? (bills ? "हो गया। आपका बिल भर दिया गया।" : "हो गया। आपका ऑर्डर हो गया।") : `हो गया। ${HI_COUNT[n] ?? n} ऑर्डर हो गए।`),
    cosign_title: "प्रियंक ने आपके नियम तय किए हैं। क्या आप सहमत हैं?",
    // "मैं" and "हूँ" are not yes words to the co-sign check: the button sends the short form
    cosign_yes: { label: "हाँ, मैं सहमत हूँ", send: "हाँ, सहमत" },
    cosign_no: { label: "अभी नहीं", send: "अभी नहीं" },
    signed_when: (time) => `${time}, अपनी पासकी से`,
    items: (n) => `${n} सामान`,
    calm: {
      no_mic: "अभी मैं आपको सुन नहीं पा रही। आप नीचे लिख सकती हैं।",
      trouble: "मेरी तरफ़ कुछ गड़बड़ हो गई। फिर से शुरू करने के लिए बड़ा बटन छुएँ।",
      start_first: "पहले शुरू करने के लिए बड़ा बटन छुएँ।",
    },
  },
};

/** The Protected card's own words (the title is in TITLES, the told line in TOLD). */
export const PROTECT_WORDS: Record<Lang, { ok: string; tap_anywhere: string; nothing_wrong: string; visa_ending: (last4: string) => string }> = {
  en: { ok: "OK", tap_anywhere: "or tap anywhere", nothing_wrong: "You did nothing wrong.", visa_ending: (d) => `Visa ending ${d}` },
  es: { ok: "Entendido", tap_anywhere: "o toque en cualquier parte", nothing_wrong: "Usted no hizo nada malo.", visa_ending: (d) => `Visa que termina en ${d}` },
  hi: { ok: "ठीक है", tap_anywhere: "या कहीं भी छुएँ", nothing_wrong: "आपकी कोई गलती नहीं है।", visa_ending: (d) => `Visa, आख़िरी अंक ${d}` },
};

/** The told clause at the end of a spoken line, per language ("…; I've told Priyank.", "Ya le avisé a Priyank.",
 * "मैंने प्रियंक को बता दिया है।"). Only a clause that ends the line is matched; one inside it stays. */
const TOLD_TAIL: RegExp[] = [
  /\s*\bI(?:'ve|’ve| have) told Priyank\s*[.!]?\s*$/i,
  /\s*\b[Yy]a le avisé a Priyank\s*[.!]?\s*$/,
  /\s*मैंने प्रियंक को बता दिया है\s*[।.]?\s*$/,
];

/**
 * The line she heard, for the Protected card that already says "Priyank has been told" under it: a told clause that
 * ends the line is left out, so the card does not say it twice. Anything else is shown as spoken.
 */
export function withoutToldTail(say: string, lang: Lang): string {
  for (const re of TOLD_TAIL) {
    if (!re.test(say)) continue;
    let rest = say.replace(re, "").replace(/[\s;,:—–-]+$/, "");
    if (!rest) return say;
    if (!/[.!?।…]$/.test(rest)) rest += lang === "hi" || /[ऀ-ॿ]$/.test(rest) ? "।" : ".";
    return rest;
  }
  return say;
}

/** Whole dollars without cents ("$60"), else two decimals: for the caps in Priyank's rules. */
function capMoney(v: unknown): string | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  if (!Number.isFinite(n) || n < 0) return null;
  return Number.isInteger(n) ? `$${n}` : `$${n.toFixed(2)}`;
}

/** One of Priyank's rules as Ruth reads it; **…** marks the amounts shown in bold. */
export interface RuleLine {
  icon: "bag" | "store" | "people" | "shield";
  text: string;
}

/** Priyank's signed rules as a short list for the co-sign screen (the rules the station reads to her). */
export function cosignLines(m: Record<string, unknown>, lang: Lang): RuleLine[] {
  const cap = capMoney(m.per_purchase_cap);
  const month = capMoney(m.monthly_cap);
  const ask = capMoney(m.approval_threshold);
  const billers = (Array.isArray(m.billers) ? m.billers : []).filter((b): b is Record<string, unknown> => !!b && typeof b === "object");
  const billerIds = new Set(billers.map((b) => String(b.merchant_id ?? "")));
  const merchants = Array.isArray(m.allowed_merchants) ? m.allowed_merchants.map(String) : [];
  const stores = merchants.filter((id) => !billerIds.has(id)).length;
  const bills = billers.length;
  const lines: RuleLine[] = [];
  if (lang === "es") {
    if (cap && month) lines.push({ icon: "bag", text: `Hasta **${cap}** cada vez, **${month}** al mes` });
    if (stores) lines.push({ icon: "store", text: `En ${stores === 1 ? "su tienda" : `sus ${stores} tiendas`}${bills ? (bills > 1 ? " y sus facturas" : " y su factura") : ""}` });
    if (ask) lines.push({ icon: "people", text: `Le pregunto a Priyank arriba de **${ask}**` });
    lines.push({ icon: "shield", text: "Nunca tarjetas de regalo, giros ni cripto" });
  } else if (lang === "hi") {
    if (cap && month) lines.push({ icon: "bag", text: `हर बार **${cap}** तक, महीने में **${month}**` });
    if (stores) lines.push({ icon: "store", text: `आपकी ${stores === 1 ? "दुकान" : `${stores} दुकानों`} पर${bills ? " और आपके बिल पर" : ""}` });
    if (ask) lines.push({ icon: "people", text: `**${ask}** से ऊपर प्रियंक से पूछूँगी` });
    lines.push({ icon: "shield", text: "गिफ्ट कार्ड, वायर या क्रिप्टो कभी नहीं" });
  } else {
    if (cap && month) lines.push({ icon: "bag", text: `Up to **${cap}** each time, **${month}** a month` });
    if (stores) lines.push({ icon: "store", text: `At your ${stores === 1 ? "store" : `${stores} stores`}${bills ? ` and your bill${bills > 1 ? "s" : ""}` : ""}` });
    if (ask) lines.push({ icon: "people", text: `I ask Priyank above **${ask}**` });
    lines.push({ icon: "shield", text: "Never gift cards, wires or crypto" });
  }
  return lines;
}

/** The outcome banner after payment and from the guards: titles and short lines. Money arrives formatted ("$8.00"). */
export interface NoticeWords {
  order_cancelled: string;
  nothing_charged: string;
  not_cancelled: string;
  already_paid: string;
  cancel_failed: string;
  refund_ask: (amount: string) => string;
  refund_done: (amount: string) => string;
  back_to_card: (last4?: string) => string;
  say_yes: string;
  refund_not_made: string;
  refund_failed: string;
  pickup_code: (code: string) => string;
  allowed_once: string;
  new_rules: string;
  you_agreed: string;
}

export const NOTICE_WORDS: Record<Lang, NoticeWords> = {
  en: {
    order_cancelled: "Order cancelled",
    nothing_charged: "Nothing was charged",
    not_cancelled: "Not cancelled",
    already_paid: "It is already paid; a return is still possible",
    cancel_failed: "Could not cancel",
    refund_ask: (amount) => `Refund ${amount}?`,
    refund_done: (amount) => `Refund ${amount}`,
    back_to_card: (last4) => (last4 ? `Back to your card ending ${last4}` : "Back to your card"),
    say_yes: "say yes to confirm",
    refund_not_made: "Refund not made",
    refund_failed: "Could not make the refund",
    pickup_code: (code) => `Pickup code ${code}`,
    allowed_once: "Allowed once",
    new_rules: "New rules from Priyank",
    you_agreed: "You agreed",
  },
  es: {
    order_cancelled: "Pedido cancelado",
    nothing_charged: "No se le cobró nada",
    not_cancelled: "No se canceló",
    already_paid: "Ya está pagado; todavía puede devolverlo",
    cancel_failed: "No se pudo cancelar",
    refund_ask: (amount) => `¿Reembolso de ${amount}?`,
    refund_done: (amount) => `Reembolso de ${amount}`,
    back_to_card: (last4) => (last4 ? `A su tarjeta que termina en ${last4}` : "A su tarjeta"),
    say_yes: "diga sí para confirmar",
    refund_not_made: "No se hizo el reembolso",
    refund_failed: "No se pudo hacer el reembolso",
    pickup_code: (code) => `Código de recogida ${code}`,
    allowed_once: "Permitido una vez",
    new_rules: "Reglas nuevas de Priyank",
    you_agreed: "Usted estuvo de acuerdo",
  },
  hi: {
    order_cancelled: "ऑर्डर रद्द हो गया",
    nothing_charged: "कोई पैसा नहीं कटा",
    not_cancelled: "रद्द नहीं हुआ",
    already_paid: "इसका भुगतान हो चुका है; सामान अब भी वापस हो सकता है",
    cancel_failed: "रद्द नहीं हो पाया",
    refund_ask: (amount) => `${amount} वापस करूँ?`,
    refund_done: (amount) => `${amount} की वापसी`,
    back_to_card: (last4) => (last4 ? `आपके ${last4} पर ख़त्म होने वाले कार्ड में` : "आपके कार्ड में"),
    say_yes: "पक्का करने के लिए हाँ कहिए",
    refund_not_made: "पैसे वापस नहीं हुए",
    refund_failed: "पैसे वापस नहीं हो पाए",
    pickup_code: (code) => `पिकअप कोड ${code}`,
    allowed_once: "एक बार की अनुमति",
    new_rules: "प्रियंक के नए नियम",
    you_agreed: "आप सहमत हुईं",
  },
};

/**
 * Money on the screen is digits in every language: the spoken forms the station's own lines use ("480 dólares con
 * 50 centavos", "480 डॉलर 50 सेंट", from cart.money) are shown as "$480.50". Words the model chose are left as said.
 */
export function moneyAsDigits(text: string): string {
  const cents = (c: string) => c.padStart(2, "0");
  return text
    .replace(/(\d+) d[óo]lar(?:es)? con (\d{1,2}) centavos?/g, (_, d: string, c: string) => `$${d}.${cents(c)}`)
    .replace(/(\d+) डॉलर (\d{1,2}) सेंट/g, (_, d: string, c: string) => `$${d}.${cents(c)}`)
    .replace(/(\d+) d[óo]lar(?:es)?(?![\p{L}])/gu, (_, d: string) => `$${d}.00`)
    .replace(/(\d+) डॉलर/g, (_, d: string) => `$${d}.00`)
    .replace(/(?<![\d.$])(\d{1,2}) centavos?(?![\p{L}])/gu, (_, c: string) => `$0.${cents(c)}`)
    .replace(/(?<![\d.$])(\d{1,2}) सेंट/g, (_, c: string) => `$0.${cents(c)}`);
}
